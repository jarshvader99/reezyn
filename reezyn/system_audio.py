"""Follow macOS's default audio devices and control the output volume.

Switching from AirPods to the built-in speakers is a system-level change: the
old input stream stays open (which can pin Bluetooth headphones in hands-free
mode) and the old output stream keeps playing into a device that may be gone.
This module watches the two default devices and reports changes, and it wraps
the one system setting Reezyn exposes in its menu: the output volume.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import threading
from collections.abc import Callable

log = logging.getLogger("reezyn")

_SYSTEM_OBJECT = 1
_SCOPE_GLOBAL = int.from_bytes(b"glob", "big")
# CoreAudio selectors are exactly four bytes; the input one is "dIn " with a
# trailing space. Truncating it silently returns a different property.
_DEFAULT_INPUT = int.from_bytes(b"dIn ", "big")
_DEFAULT_OUTPUT = int.from_bytes(b"dOut", "big")

VOLUME_LEVELS = (20, 40, 60, 80, 100)


class AudioPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("selector", ctypes.c_uint32),
        ("scope", ctypes.c_uint32),
        ("element", ctypes.c_uint32),
    ]


_library: ctypes.CDLL | None = None
_library_unavailable = False
_library_lock = threading.Lock()


def _coreaudio() -> ctypes.CDLL | None:
    global _library, _library_unavailable
    with _library_lock:
        if _library is not None:
            return _library
        if _library_unavailable:
            return None
        try:
            library = ctypes.CDLL(
                "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
            )
            library.AudioObjectGetPropertyData.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(AudioPropertyAddress),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.c_void_p,
            ]
            library.AudioObjectGetPropertyData.restype = ctypes.c_int32
        except OSError as exc:
            log.debug("CoreAudio is unavailable: %s", exc)
            _library_unavailable = True
            return None
        _library = library
        return library


def _read_object_id(object_id: int, selector: int) -> int:
    library = _coreaudio()
    if library is None:
        return 0
    address = AudioPropertyAddress(selector, _SCOPE_GLOBAL, 0)
    value = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(value))
    status = library.AudioObjectGetPropertyData(
        ctypes.c_uint32(object_id),
        ctypes.byref(address),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(value),
    )
    return int(value.value) if status == 0 else 0


def default_input_device() -> int:
    return _read_object_id(_SYSTEM_OBJECT, _DEFAULT_INPUT)


def default_output_device() -> int:
    return _read_object_id(_SYSTEM_OBJECT, _DEFAULT_OUTPUT)


def device_ids() -> tuple[int, int]:
    """The current default input and output device ids."""

    return (default_input_device(), default_output_device())


class DeviceWatcher:
    """Poll the default devices and report a change once per switch.

    CoreAudio has property listeners, but a two-second poll is simpler, cheap,
    and has no callback-threading rules to get wrong. A change is reported
    after the new default is observed, never repeatedly.
    """

    def __init__(
        self,
        on_input_change: Callable[[int], None],
        on_output_change: Callable[[int], None],
        interval: float = 2.0,
        reader: Callable[[], tuple[int, int]] = device_ids,
    ) -> None:
        self._on_input = on_input_change
        self._on_output = on_output_change
        self._interval = max(0.25, interval)
        self._reader = reader
        self._last: tuple[int, int] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._last = None
        self._thread = threading.Thread(
            target=self._run, name="reezyn-devices", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)

    def poll_once(self) -> tuple[bool, bool]:
        try:
            current = self._reader()
        except Exception:
            log.debug("could not read the default audio devices", exc_info=True)
            return (False, False)

        with self._lock:
            if self._last is None:
                self._last = current
                return (False, False)
            previous = self._last
            self._last = current

        input_changed = previous[0] != current[0]
        output_changed = previous[1] != current[1]
        if input_changed:
            self._call(self._on_input, current[0], "input")
        if output_changed:
            self._call(self._on_output, current[1], "output")
        return (input_changed, output_changed)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._interval)

    @staticmethod
    def _call(callback: Callable[[int], None], device: int, label: str) -> None:
        try:
            callback(device)
        except Exception:
            log.exception("audio %s change handler failed", label)


def clamp_volume(percent: int) -> int:
    return max(0, min(100, int(percent)))


def _osascript(script: str) -> str | None:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("osascript failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def output_volume() -> int | None:
    """The Mac's current output volume, 0–100."""

    value = _osascript("output volume of (get volume settings)")
    if value is None:
        return None
    try:
        return clamp_volume(int(value))
    except ValueError:
        return None


def set_output_volume(percent: int) -> bool:
    value = clamp_volume(percent)
    result = _osascript(f"set volume output volume {value}")
    return result is not None


def describe() -> str:
    """A short line for diagnostics."""

    input_id, output_id = device_ids()
    volume = output_volume()
    level = f"{volume}%" if volume is not None else "unknown"
    return f"input {input_id or '?'} · output {output_id or '?'} · volume {level}"
