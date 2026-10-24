"""On-device speech recognition through Apple's Speech framework.

Each utterance gets its own request so the audio can be ended deterministically
at a pause, which is what makes the Siri-like commit possible. Partial results
stream back, and contextual strings bias the model toward the wake phrase and
the vocabulary of the current project.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("reezyn")


class SpeechUnavailable(RuntimeError):
    pass


def authorization_status() -> str:
    try:
        import Speech

        return _status_name(Speech.SFSpeechRecognizer.authorizationStatus())
    except Exception:
        return "unavailable"


def request_authorization(callback: Any) -> None:
    try:
        import Speech

        Speech.SFSpeechRecognizer.requestAuthorization_(
            lambda status: callback(_status_name(status))
        )
    except Exception:
        callback("unavailable")


def _status_name(status: Any) -> str:
    return {
        0: "not_determined",
        1: "denied",
        2: "restricted",
        3: "authorized",
    }.get(int(status), "unknown")


class Recognition:
    """One in-flight utterance."""

    def __init__(self, engine: "SpeechEngine", context: list[str]) -> None:
        import Speech

        self._engine = engine
        self._lock = threading.RLock()
        self._partial = ""
        self._final = ""
        self._done = False
        self._error = ""

        request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        request.setShouldReportPartialResults_(True)
        if engine.on_device:
            request.setRequiresOnDeviceRecognition_(True)
        if context:
            request.setContextualStrings_(context)
        self._request = request
        self._task = engine.recognizer.recognitionTaskWithRequest_resultHandler_(
            request, self._handler
        )

    def _handler(self, result: Any, error: Any) -> None:
        with self._lock:
            if result is not None:
                transcription = result.bestTranscription()
                text = str(transcription.formattedString()) if transcription else ""
                if text:
                    self._partial = text
                if bool(result.isFinal()):
                    self._final = text
                    self._done = True
            if error is not None:
                # 203 is "no speech detected", which is a normal outcome of a
                # wake-word probe that turned out to be noise.
                self._error = str(error)
                self._done = True

    @property
    def partial(self) -> str:
        with self._lock:
            return self._partial

    @property
    def done(self) -> bool:
        with self._lock:
            return self._done

    def append(self, buffer: Any) -> None:
        try:
            self._request.appendAudioPCMBuffer_(buffer)
        except Exception:
            log.debug("could not append audio", exc_info=True)

    def end(self, timeout: float = 1.5) -> str:
        try:
            self._request.endAudio()
        except Exception:
            log.debug("endAudio failed", exc_info=True)
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline and not self.done:
            time.sleep(0.02)
        with self._lock:
            return self._final or self._partial

    def cancel(self) -> None:
        try:
            self._task.cancel()
        except Exception:
            log.debug("cancel failed", exc_info=True)
        try:
            self._request.endAudio()
        except Exception:
            pass
        with self._lock:
            self._done = True


class SpeechEngine:
    def __init__(self, locale_id: str = "en-US", on_device: bool = True) -> None:
        import AVFoundation
        import Speech

        self.locale_id = locale_id
        self.on_device = on_device
        self._recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(
            AVFoundation.NSLocale.localeWithLocaleIdentifier_(locale_id)
        )

    @property
    def recognizer(self) -> Any:
        return self._recognizer

    def available(self) -> bool:
        try:
            return bool(self._recognizer is not None and self._recognizer.isAvailable())
        except Exception:
            return False

    def supports_on_device(self) -> bool:
        try:
            return bool(self._recognizer.supportsOnDeviceRecognition())
        except Exception:
            return False

    def begin(self, context: list[str]) -> Recognition:
        if not self.available():
            raise SpeechUnavailable(
                f"Speech recognition for {self.locale_id} is unavailable."
            )
        return Recognition(self, [item for item in context if item][:50])


def locale_for_language(lang: str | None, override: str = "") -> str:
    """Map a Kokoro language code onto a Speech locale."""

    if override.strip():
        return override.strip()
    base = (lang or "en-us").lower()
    return {
        "en-us": "en-US",
        "en-gb": "en-GB",
        "es": "es-ES",
        "fr-fr": "fr-FR",
    }.get(base, "en-US")
