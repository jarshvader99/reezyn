"""Run the bits of Cocoa that require the main thread.

AppKit is not a thread-safe framework. Reading the frontmost application or
playing an NSSound from a voice worker can corrupt AppKit's own memory and
crash the menu-bar app minutes later, which looks like an unrelated layout
failure in the crash report. Everything in Reezyn that touches AppKit off the
main thread goes through here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any


def _autorelease_pool() -> Any:
    try:
        import objc

        return objc.autorelease_pool()
    except Exception:
        return nullcontext()


def call_on_main(func: Callable[..., Any], *args: Any, timeout: float = 0.6) -> Any:
    """Run ``func`` on the AppKit main thread and return its result.

    The caller blocks. On the main thread the call is direct, which is what
    tests and menu callbacks already do. If AppKit has no run loop (headless
    use), do the call on the worker with a pool rather than deadlocking.
    """

    if threading.current_thread() is threading.main_thread():
        with _autorelease_pool():
            return func(*args)

    try:
        from PyObjCTools import AppHelper
    except Exception:
        with _autorelease_pool():
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
        with _autorelease_pool():
            return func(*args)
    if not done.wait(timeout):
        raise TimeoutError("the main thread did not answer in time")
    if "error" in box:
        raise box["error"]
    return box.get("value")
