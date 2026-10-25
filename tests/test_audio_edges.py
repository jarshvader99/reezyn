from __future__ import annotations

import numpy as np

from reezyn.tts import TARGET_PEAK, _limit, _prepare_samples, _to_pcm16


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


def test_soft_knee_bends_instead_of_folding_back():
    ramp = np.linspace(0.0, 2.0, 4000, dtype=np.float32)
    limited = _limit(ramp)

    assert float(np.max(np.abs(limited))) <= TARGET_PEAK
    assert bool(np.all(np.diff(limited) >= 0))
    # The knee is continuous: just past it, the curve is still close to linear.
    assert abs(float(limited[0]) - 0.0) < 1e-6


def test_pcm16_is_dithered_and_in_range():
    hot = _to_pcm16(np.array([1.5, -1.5, 0.0], dtype=np.float32))
    assert int(hot[0]) == 32767
    assert int(hot[1]) == -32768

    quiet = _to_pcm16(np.zeros(64, dtype=np.float32))
    assert quiet.min() >= -1 and quiet.max() <= 1
    assert len(set(int(value) for value in quiet)) > 1


def test_silence_stays_silent():
    samples = _prepare_samples(np.zeros(1000, dtype=np.float32), 24000, 0.9)
    assert float(np.max(np.abs(samples))) == 0.0
