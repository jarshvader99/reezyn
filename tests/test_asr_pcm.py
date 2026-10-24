from __future__ import annotations

import numpy as np

from reezyn.voice.asr import pcm_buffer


def test_pcm_buffer_keeps_float_samples_and_rate():
    samples = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)
    buffer = pcm_buffer(samples, 16000)

    assert buffer.frameLength() == 4
    assert int(buffer.format().sampleRate()) == 16000
    assert int(buffer.format().channelCount()) == 1

    view = buffer.floatChannelData()[0].as_buffer(4)
    copied = np.frombuffer(view, dtype=np.float32)
    assert np.allclose(copied, samples)


def test_pcm_buffer_clips_out_of_range_values():
    buffer = pcm_buffer(np.array([-2.0, 2.0], dtype=np.float32), 48000)
    copied = np.frombuffer(buffer.floatChannelData()[0].as_buffer(2), dtype=np.float32)
    assert list(copied) == [-1.0, 1.0]
