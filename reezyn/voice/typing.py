"""Type dictated text into the focused application, and refuse when unsure.

Synthetic keystrokes go wherever the focus is, so three guards run first:
Accessibility trust, a frontmost-app allowlist, and (when available) the focused
element's role. Nothing here submits text on its own; Return is a separate,
explicit action.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from ..main_thread import call_on_main

log = logging.getLogger("reezyn")

RETURN_KEY = 36
ESCAPE_KEY = 53
BACKSPACE_KEY = 51
CHUNK_UNITS = 16
CHUNK_DELAY_S = 0.008

TEXT_ROLES = frozenset({"AXTextArea", "AXTextField", "AXComboBox"})


def accessibility_trusted() -> bool:
    try:
        import Quartz

        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception:
        return False


def request_accessibility() -> None:
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        log.debug("could not prompt for accessibility", exc_info=True)


_FRONTMOST_CACHE_S = 0.15
_frontmost_value = ""
_frontmost_at = 0.0


def _frontmost_bundle_id() -> str:
    from AppKit import NSWorkspace

    application = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(application.bundleIdentifier() or "")


def frontmost_bundle_id(fresh: bool = False) -> str:
    """The bundle id of the frontmost app, read on the AppKit main thread.

    The voice loop calls this while it is deciding whether to listen, so a
    short cache keeps it from asking the main thread twenty times a second.
    Callers that are about to type pass ``fresh=True``; a 150 ms stale answer
    is fine for arming the microphone but not for choosing where keys go.
    """

    global _frontmost_value, _frontmost_at
    now = time.monotonic()
    if not fresh and now - _frontmost_at < _FRONTMOST_CACHE_S:
        return _frontmost_value
    try:
        value = str(call_on_main(_frontmost_bundle_id, timeout=0.5) or "")
    except Exception:
        return ""
    _frontmost_value = value
    _frontmost_at = now
    return value


def focused_role() -> str:
    """The AX role of the focused control, or "" when it cannot be read."""

    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
        )

        error, focused = AXUIElementCopyAttributeValue(
            AXUIElementCreateSystemWide(), "AXFocusedUIElement", None
        )
        if error != 0 or focused is None:
            return ""
        error, role = AXUIElementCopyAttributeValue(focused, "AXRole", None)
        return str(role) if error == 0 and role is not None else ""
    except Exception:
        return ""


def focus_ok(allow_apps: Iterable[str], require_text_field: bool = True) -> bool:
    """A silent version of the guard, used to decide whether to listen at all.

    This is what keeps the microphone from transcribing a room when no terminal
    input is focused: no wake-word probe starts anywhere else.
    """

    allowed = list(allow_apps)
    bundle = frontmost_bundle_id()
    if allowed and bundle not in allowed:
        return False
    if require_text_field:
        role = focused_role()
        # An unreadable role means Accessibility is not granted yet; the insert
        # guard reports that properly, and dictation can still be prepared.
        if role and role not in TEXT_ROLES:
            return False
    return True


def guard(allow_apps: Iterable[str], require_text_field: bool = True) -> tuple[bool, str]:
    if not accessibility_trusted():
        return (
            False,
            "Grant Reezyn Accessibility access in System Settings → Privacy & "
            "Security → Accessibility.",
        )
    bundle = frontmost_bundle_id(fresh=True)
    allowed = list(allow_apps)
    if allowed and bundle not in allowed:
        return (
            False,
            f"{bundle or 'The frontmost app'} is not on the voice allowlist, so "
            "nothing was typed.",
        )
    if require_text_field:
        role = focused_role()
        if role and role not in TEXT_ROLES:
            return False, "The focused control is not a text field."
    return True, ""


def _post_unicode(text: str) -> None:
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    units = len(text.encode("utf-16-le")) // 2
    if units <= 0:
        return
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, 0, down)
        Quartz.CGEventKeyboardSetUnicodeString(event, units, text)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _post_key(code: int) -> None:
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, code, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def chunks(text: str, limit: int = CHUNK_UNITS) -> Iterable[str]:
    """Split text without cutting a surrogate pair in half."""

    buffer = ""
    units = 0
    for char in text:
        size = len(char.encode("utf-16-le")) // 2
        if units + size > limit and buffer:
            yield buffer
            buffer = ""
            units = 0
        buffer += char
        units += size
    if buffer:
        yield buffer


class Typer:
    """The injected seam for tests: every keystroke decision goes through here."""

    def __init__(
        self,
        allow_apps: Iterable[str],
        require_text_field: bool = True,
        insert: Callable[[str], int] | None = None,
        return_key: Callable[[], None] | None = None,
        escape_key: Callable[[], None] | None = None,
        backspace: Callable[[int], None] | None = None,
        check: Callable[[], tuple[bool, str]] | None = None,
        focus: Callable[[], bool] | None = None,
    ) -> None:
        self._allow_apps = list(allow_apps)
        self._require_text_field = require_text_field
        self._insert = insert or insert_text
        self._return = return_key or press_return
        self._escape = escape_key or press_escape
        self._backspace = backspace or backspace_keys
        self._check = check
        self._focus = focus

    def focus_ok(self) -> bool:
        if self._focus is not None:
            return self._focus()
        return focus_ok(self._allow_apps, self._require_text_field)

    def guard(self) -> tuple[bool, str]:
        if self._check is not None:
            return self._check()
        return guard(self._allow_apps, self._require_text_field)

    def insert_text(self, text: str) -> int:
        return self._insert(text)

    def press_return(self) -> None:
        self._return()

    def press_escape(self) -> None:
        self._escape()

    def backspace(self, count: int) -> None:
        self._backspace(count)


def insert_text(text: str) -> int:
    count = 0
    for chunk in chunks(text):
        _post_unicode(chunk)
        count += len(chunk)
        time.sleep(CHUNK_DELAY_S)
    return count


def press_return() -> None:
    _post_key(RETURN_KEY)


def press_escape() -> None:
    _post_key(ESCAPE_KEY)


def backspace_keys(count: int) -> None:
    for _ in range(max(0, count)):
        _post_key(BACKSPACE_KEY)
        time.sleep(0.004)
