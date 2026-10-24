from __future__ import annotations

import numpy as np

from reezyn.tts import _prepare_samples


def _tone(seconds: float = 0.25, rate: int = 24000) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (0.6 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_edges_are_faded_and_the_body_is_kept():
    samples = _prepare_samples(_tone(), 24000, 0.9)
    assert abs(float(samples[0])) < 1e-6
    assert abs(float(samples[-1])) < 1e-6
    body = samples[samples.size // 4 : 3 * samples.size // 4]
    assert float(np.max(np.abs(body))) > 0.3


def test_peak_guard_never_exceeds_the_target():
    loud = (_tone() * 4.0).astype(np.float32)
    samples = _prepare_samples(loud, 24000, 1.0)
    assert float(np.max(np.abs(samples))) <= 0.9501


def test_silence_stays_silent():
    samples = _prepare_samples(np.zeros(1000, dtype=np.float32), 24000, 0.9)
    assert float(np.max(np.abs(samples))) == 0.0
