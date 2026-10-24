"""What the phone can do to the Mac, and the guards around it.

Synthetic keystrokes land wherever the focus is, so every action re-checks the
frontmost app and the focused control. When the screen is locked macOS drops
keystrokes entirely; replies are queued and flushed after unlock instead of
being lost.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from ..voice import typing

log = logging.getLogger("reezyn")

MAX_TEXT = 4000
QUEUE_LIMIT = 20
QUEUE_TTL_S = 1800.0
ACTIVATE_WAIT_S = 1.6


def _autorelease_pool():
    """Workers do not inherit the main thread's AppKit autorelease pool."""

    try:
        import objc

        return objc.autorelease_pool()
    except Exception:
        return nullcontext()


def screen_locked() -> bool:
    try:
        import Quartz

        session = Quartz.CGSessionCopyCurrentDictionary()
        return bool(session and session.get("CGSSessionScreenIsLocked"))
    except Exception:
        return False


def activate_app(bundle: str) -> bool:
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
        if not apps:
            return False
        app = apps[0]
        app.unhide()
        return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        log.debug("could not activate %s", bundle, exc_info=True)
        return False


def app_name(bundle: str) -> str:
    try:
        from AppKit import NSRunningApplication

        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
        if apps:
            return str(apps[0].localizedName() or bundle)
    except Exception:
        pass
    return bundle


@dataclass
class _Pending:
    text: str
    submit: bool
    created: float = field(default_factory=time.monotonic)


class RemoteActions:
    def __init__(
        self,
        config: Any,
        allow_apps: Iterable[str],
        require_text_field: bool = True,
        *,
        typer: Any | None = None,
        frontmost: Callable[[], str] | None = None,
        activate: Callable[[str], bool] | None = None,
        locked: Callable[[], bool] | None = None,
        trusted: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        start_caffeinate: bool = True,
    ) -> None:
        self._config = config
        self._apps = list(allow_apps)
        self._typer = typer or typing.Typer(self._apps, require_text_field)
        # Focus changes must be read fresh: a cached app would let Send type
        # into whatever was frontmost a fraction of a second earlier.
        self._frontmost = frontmost or (lambda: typing.frontmost_bundle_id(fresh=True))
        self._activate = activate or activate_app
        self._locked = locked or screen_locked
        self._trusted = trusted or typing.accessibility_trusted
        self._clock = clock
        self._want_caffeinate = start_caffeinate
        self._lock = threading.RLock()
        self._appkit_lock = threading.RLock()
        self._queue: deque[_Pending] = deque(maxlen=QUEUE_LIMIT)
        self._last_terminal = ""
        self._last_error = ""
        # Snapshots are built on HTTP/SSE threads. AppKit and Quartz objects
        # must not be created there without a pool, so the tracker refreshes
        # this small cache and snapshots only read it.
        self._cached_front = ""
        self._cached_locked = False
        self._cached_can_type = True
        self._cached_terminal_name = ""
        self._cached_front_name = ""
        self._name_cache: dict[str, str] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._caffeinate: subprocess.Popen[bytes] | None = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
        self._refresh_cache()
        if self._want_caffeinate and getattr(self._config, "keep_awake", True):
            self._start_caffeinate()
        for target, name in ((self._track, "reezyn-remote-focus"), (self._drain, "reezyn-remote-queue")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop.set()
            self._queue.clear()
        self._stop_caffeinate()

    def _start_caffeinate(self) -> None:
        try:
            self._caffeinate = subprocess.Popen(
                ["caffeinate", "-dims"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            log.debug("caffeinate is unavailable", exc_info=True)

    def _stop_caffeinate(self) -> None:
        process = self._caffeinate
        self._caffeinate = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    # -- observation -------------------------------------------------------

    def _query(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run Quartz and Accessibility work off the main thread safely.

        CGEvent posting and AX queries are fine on a worker, provided the
        thread has a pool for the objects PyObjC autoreleases.
        """

        with self._appkit_lock, _autorelease_pool():
            return func(*args)

    def _main_call(self, func: Callable[..., Any], *args: Any, timeout: float = 3.0) -> Any:
        """Run an AppKit call on the main thread, where Cocoa expects it.

        NSWorkspace and NSRunningApplication are not safe to touch from the
        HTTP and tracker threads. Doing so corrupts AppKit's own memory and
        eventually crashes the menu-bar app, so those calls hop to the main
        run loop and wait briefly for the answer.
        """

        if threading.current_thread() is threading.main_thread():
            with _autorelease_pool():
                return func(*args)

        try:
            from PyObjCTools import AppHelper
        except Exception:
            with self._appkit_lock, _autorelease_pool():
                return func(*args)

        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            with _autorelease_pool():
                try:
                    box["value"] = func(*args)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    box["error"] = exc
                finally:
                    done.set()

        try:
            AppHelper.callAfter(run)
        except Exception:
            # No AppKit run loop (headless service): the worker call is the
            # only option, and the caller still holds a pool for it.
            with self._appkit_lock, _autorelease_pool():
                return func(*args)
        if not done.wait(timeout):
            raise TimeoutError("the Mac's menu-bar app did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _name(self, bundle: str) -> str:
        if not bundle:
            return ""
        with self._lock:
            cached = self._name_cache.get(bundle)
        if cached:
            return cached
        name = str(self._main_call(app_name, bundle) or bundle)
        with self._lock:
            self._name_cache[bundle] = name
        return name

    def _refresh_cache(self) -> None:
        try:
            with self._appkit_lock, _autorelease_pool():
                locked = bool(self._locked())
                trusted = bool(self._trusted())
            front = str(self._main_call(self._frontmost) or "")
        except Exception:
            log.debug("focus tracking failed", exc_info=True)
            return
        with self._lock:
            self._cached_front = front
            self._cached_locked = locked
            self._cached_can_type = trusted
            if front and (not self._apps or front in self._apps):
                self._last_terminal = front
            terminal = self._last_terminal
        terminal_name = self._name(terminal)
        front_name = self._name(front)
        with self._lock:
            self._cached_terminal_name = terminal_name
            self._cached_front_name = front_name

    def _track(self) -> None:
        while not self._stop.is_set():
            self._refresh_cache()
            self._stop.wait(1.0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "locked": self._cached_locked,
                "queued": len(self._queue),
                "terminal": self._cached_terminal_name,
                "frontmost": self._cached_front_name,
                "can_type": self._cached_can_type,
                "keep_awake": bool(self._caffeinate is not None),
                "error": self._last_error,
            }

    # -- focus -------------------------------------------------------------

    def focus_terminal(self) -> tuple[bool, str]:
        try:
            front = str(self._main_call(self._frontmost) or "")
        except Exception:
            front = ""
        if front and (not self._apps or front in self._apps):
            return True, ""
        with self._lock:
            target = self._last_terminal
        if not target:
            # Nothing tracked yet: try the allowlisted apps that are running,
            # preferring one with a visible window (activate reports that).
            for bundle in self._apps:
                try:
                    if self._main_call(self._activate, bundle):
                        target = bundle
                        with self._lock:
                            self._last_terminal = bundle
                        break
                except Exception:
                    continue
            if not target:
                return False, "No terminal has been used yet. Focus your agent's terminal once on the Mac."
        try:
            activated = bool(self._main_call(self._activate, target))
        except Exception:
            activated = False
        if not activated:
            return False, f"{self._name(target)} is not running on the Mac anymore."
        deadline = self._clock() + ACTIVATE_WAIT_S
        while self._clock() < deadline:
            try:
                if self._main_call(self._frontmost, timeout=1.0) == target:
                    return True, ""
            except Exception:
                pass
            time.sleep(0.08)
        return False, f"Could not bring {self._name(target)} to the front."

    # -- actions -----------------------------------------------------------

    def say(self, text: str, submit: bool = True) -> dict[str, Any]:
        text = _clean(text)
        if not text:
            return _result(False, "There is nothing to send.")
        if len(text) > MAX_TEXT:
            return _result(False, f"That reply is too long ({len(text)} characters, {MAX_TEXT} max).")

        if self._query(self._locked):
            if getattr(self._config, "queue_when_locked", True):
                dropped = self._enqueue(text, submit)
                note = "Mac is locked. The reply is queued and will be sent after you unlock it."
                if dropped:
                    note += " The oldest queued reply was dropped."
                return _result(False, note, queued=True)
            return _result(False, "The Mac is locked, so it cannot receive keystrokes.")

        focused, reason = self.focus_terminal()
        if not focused:
            return _result(False, reason)

        ok, reason = self._main_call(self._typer.guard)
        if not ok:
            self._last_error = reason
            return _result(False, reason)
        if self._query(self._locked):
            return self._enqueue_after_check(text, submit)
        try:
            self._query(self._typer.insert_text, text)
            if submit:
                self._query(self._typer.press_return)
        except Exception as exc:
            log.warning("remote typing failed: %s", exc)
            return _result(False, f"Typing failed: {exc}")
        self._last_error = ""
        return _result(True, "Sent." if submit else "Typed into the terminal.")

    def _enqueue_after_check(self, text: str, submit: bool) -> dict[str, Any]:
        if not getattr(self._config, "queue_when_locked", True):
            return _result(False, "The Mac locked before the reply could be typed.")
        self._enqueue(text, submit)
        return _result(False, "The Mac locked while sending. The reply is queued.", queued=True)

    def _enqueue(self, text: str, submit: bool) -> bool:
        with self._lock:
            dropped = len(self._queue) >= QUEUE_LIMIT
            self._queue.append(_Pending(text=text, submit=submit, created=self._clock()))
            return dropped

    def answer_permission(self, allow: bool) -> dict[str, Any]:
        if self._query(self._locked):
            return _result(False, "The Mac is locked, so the permission prompt cannot be answered.")
        if not self._query(self._trusted):
            return _result(
                False,
                "Grant Reezyn Accessibility access in System Settings → Privacy & "
                "Security → Accessibility, then try again.",
            )
        focused, reason = self.focus_terminal()
        if not focused:
            return _result(False, reason)
        try:
            if allow:
                self._query(self._typer.press_return)
            else:
                self._query(self._typer.press_escape)
        except Exception as exc:
            return _result(False, f"Could not answer the prompt: {exc}")
        return _result(True, "Allowed." if allow else "Denied.")

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain_once()
            except Exception:
                log.debug("remote queue drain failed", exc_info=True)
            self._stop.wait(1.2)

    def _drain_once(self) -> None:
        with self._lock:
            if not self._queue:
                return
        if self._query(self._locked):
            self._expire()
            return
        if not getattr(self._config, "queue_when_locked", True):
            with self._lock:
                self._queue.clear()
            return
        focused, _reason = self.focus_terminal()
        if not focused:
            self._expire()
            return
        ok, _reason = self._main_call(self._typer.guard)
        if not ok:
            self._expire()
            return
        with self._lock:
            if not self._queue:
                return
            item = self._queue[0]
        try:
            self._query(self._typer.insert_text, item.text)
            if item.submit:
                self._query(self._typer.press_return)
        except Exception:
            log.debug("queued reply failed; leaving it in place", exc_info=True)
            self._expire()
            return
        with self._lock:
            if self._queue and self._queue[0] is item:
                self._queue.popleft()
        log.info("remote reply flushed after unlock")

    def _expire(self) -> None:
        now = self._clock()
        with self._lock:
            while self._queue and now - self._queue[0].created > QUEUE_TTL_S:
                self._queue.popleft()


def _clean(text: str) -> str:
    return text.replace("\x00", "").strip()


def _result(ok: bool, message: str, queued: bool = False) -> dict[str, Any]:
    return {"ok": ok, "queued": queued, "message": message}
