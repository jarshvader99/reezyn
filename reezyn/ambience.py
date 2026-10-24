"""A quiet, generated presence bed that ducks under narration.

Nothing ships with Reezyn: the bed is low-passed noise synthesized on the fly,
faded in slowly, and dipped while a line is being spoken. It is opt-in and only
runs through sounddevice; the afplay fallback cannot mix a second stream.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

import numpy as np

from .config import AmbienceConfig

log = logging.getLogger("reezyn")

DUCK_FACTOR = 0.25
LEVEL_SCALE = 0.22
DUCK_ATTACK_S = 0.12
DUCK_RELEASE_S = 1.1
LFO_HZ = 0.055
LFO_DEPTH = 0.07
_WINDOW = 33


class Ambience:
    """One continuous output stream, shaped in its callback."""

    def __init__(self, config: AmbienceConfig) -> None:
        self.config = config
        self._stream: Any | None = None
        self._rate = 24000
        self._ducked = False
        self._gain = 0.0
        self._phase = 0.0
        self._tail = np.zeros(_WINDOW - 1, dtype=np.float32)
        self._rng = np.random.default_rng()
        self._error = ""
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    def start(self) -> bool:
        if self._stream is not None:
            return True
        if not self.config.enabled:
            return True
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            with self._lock:
                self._error = f"sounddevice is unavailable: {exc}"
            return False

        try:
            device = sd.query_devices(kind="output")
            rate = int(device.get("default_samplerate") or 24000)
            self._rate = rate if 22050 <= rate <= 96000 else 24000
            stream = sd.OutputStream(
                samplerate=self._rate,
                channels=2,
                dtype="float32",
                blocksize=0,
                latency="low",
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            return False

        self._stream = stream
        self._gain = 0.0
        self._phase = 0.0
        self._tail = np.zeros(_WINDOW - 1, dtype=np.float32)
        with self._lock:
            self._error = ""
        return True

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.debug("presence bed stop failed: %s", exc)

    def set_ducked(self, ducked: bool) -> None:
        self._ducked = ducked

    def _target_gain(self) -> float:
        volume = max(0.0, min(1.0, self.config.volume))
        return volume * LEVEL_SCALE * (DUCK_FACTOR if self._ducked else 1.0)

    def _render(self, frames: int) -> np.ndarray:
        if frames <= 0:
            return np.zeros(0, dtype=np.float32)

        white = self._rng.standard_normal(frames).astype(np.float32)
        window = np.hanning(_WINDOW).astype(np.float32)
        window /= window.sum()
        padded = np.concatenate((self._tail, white))
        filtered = np.convolve(padded, window, mode="valid")
        self._tail = padded[-_WINDOW + 1 :]

        phase = (self._phase + np.arange(frames, dtype=np.float32)) / self._rate
        breath = 1.0 + LFO_DEPTH * np.sin(2.0 * math.pi * LFO_HZ * phase)
        self._phase += frames / self._rate

        target = self._target_gain()
        tau = DUCK_ATTACK_S if target < self._gain else DUCK_RELEASE_S
        coefficient = 1.0 - math.exp(-frames / (tau * self._rate))
        next_gain = self._gain + (target - self._gain) * coefficient
        gains = np.linspace(self._gain, next_gain, frames, endpoint=True, dtype=np.float32)
        self._gain = float(next_gain)
        return filtered * gains * breath.astype(np.float32)

    def _callback(self, outdata: Any, frames: int, _time: Any, status: Any) -> None:
        if status:
            log.debug("presence bed status: %s", status)
        try:
            chunk = self._render(frames)
        except Exception as exc:
            log.warning("presence bed render failed: %s", exc)
            outdata.fill(0)
            return
        outdata[:] = chunk.reshape(-1, 1)
