"""Microphone capture through AVAudioEngine, with voice processing on.

Voice processing gives us Apple's echo cancellation, which is what makes it
possible to listen while Reezyn itself is speaking. The tap receives the same
AVAudioPCMBuffer the recognizer wants, plus a level for the wake-word gate.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

log = logging.getLogger("reezyn")

TAP_FRAMES = 2048


class MicrophoneError(RuntimeError):
    pass


class Microphone:
    """One input stream for the life of the app."""

    def __init__(
        self,
        on_frame: Callable[[Any, float], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_frame = on_frame
        self._on_error = on_error
        self._engine: Any | None = None
        self._node: Any | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._engine is not None

    def start(self) -> bool:
        with self._lock:
            if self._engine is not None:
                return True
            try:
                import AVFoundation
            except Exception as exc:
                self._fail(f"Speech audio is unavailable: {exc}")
                return False

            engine = AVFoundation.AVAudioEngine.alloc().init()
            node = engine.inputNode()
            try:
                # Echo cancellation: lets the mic stay open while Reezyn talks.
                node.setVoiceProcessingEnabled_error_(True)
            except Exception:
                log.debug("voice processing unavailable", exc_info=True)
            try:
                format = node.inputFormatForBus_(0)
                node.installTapOnBus_bufferSize_format_block_(
                    0, TAP_FRAMES, format, self._handle_buffer
                )
                engine.prepare()
                ok, error = engine.startAndReturnError_(None)
            except Exception as exc:
                self._fail(str(exc))
                return False
            if not ok:
                self._fail(str(error) or "The microphone did not start.")
                return False
            self._engine = engine
            self._node = node
            log.info("microphone started")
            return True

    def stop(self) -> None:
        with self._lock:
            node = self._node
            engine = self._engine
            self._node = None
            self._engine = None
        try:
            if node is not None:
                node.removeTapOnBus_(0)
        except Exception:
            log.debug("could not remove the audio tap", exc_info=True)
        try:
            if engine is not None:
                engine.stop()
        except Exception:
            log.debug("could not stop the audio engine", exc_info=True)

    def _handle_buffer(self, buffer: Any, when: Any) -> None:
        try:
            samples = _samples(buffer)
        except Exception:
            return
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(samples))))
        try:
            self._on_frame(buffer, rms)
        except Exception:
            log.debug("frame handler failed", exc_info=True)

    def _fail(self, message: str) -> None:
        log.warning("microphone unavailable: %s", message)
        if self._on_error is not None:
            try:
                self._on_error(message)
            except Exception:
                pass


def _samples(buffer: Any) -> np.ndarray:
    frames = int(buffer.frameLength())
    channels = buffer.floatChannelData()
    if frames <= 0 or channels is None:
        return np.empty(0, dtype=np.float32)
    return np.array(channels[0][:frames], dtype=np.float32)


def microphone_status() -> str:
    try:
        import AVFoundation

        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
    except Exception:
        return "unavailable"
    return _status_name(status)


def request_microphone_access(callback: Callable[[bool], None]) -> None:
    try:
        import AVFoundation

        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio,
            lambda granted: callback(bool(granted)),
        )
    except Exception:
        callback(False)


def _status_name(status: Any) -> str:
    # AVAuthorizationStatus
    return {
        0: "not_determined",
        1: "restricted",
        2: "denied",
        3: "authorized",
    }.get(int(status), "unknown")
