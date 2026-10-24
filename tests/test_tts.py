from __future__ import annotations

import numpy as np

from reezyn.tts import _prepare_samples


def test_prepare_samples_removes_dc_and_keeps_headroom():
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    signal = (0.2 + 0.3 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)

    prepared = _prepare_samples(signal, sample_rate, 0.9)

    assert prepared.dtype == np.float32
    assert abs(float(np.mean(prepared))) < 1e-3
    assert float(np.max(np.abs(prepared))) <= 0.98 + 1e-4


def test_prepare_samples_scales_hot_signal_instead_of_clipping():
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    signal = (1.5 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)

    prepared = _prepare_samples(signal, sample_rate, 1.0)

    assert float(np.max(np.abs(prepared))) <= 0.98 + 1e-4
