from __future__ import annotations

import numpy as np

from reezyn.languages import DEFAULT_PACK
from reezyn.tts import MAKEUP_GAIN, _prepare_samples, _speech_pieces


def test_a_short_line_is_synthesized_as_one_piece():
    text = "I'm checking what changed."
    assert _speech_pieces(text, DEFAULT_PACK) == [text]


def test_a_long_line_is_split_on_sentence_boundaries():
    text = (
        "The refresh path rotates the cookie every time. "
        "But the old token is never invalidated, which is why it still works."
    )
    pieces = _speech_pieces(text, DEFAULT_PACK)

    assert len(pieces) == 2
    assert " ".join(pieces) == text
    assert pieces[0].endswith(".")


def test_one_long_sentence_is_split_on_its_clauses():
    text = (
        "The refresh path rotates the cookie but never invalidates the old token, "
        "and that is why the stale session still works, which surprised me at first."
    )
    pieces = _speech_pieces(text, DEFAULT_PACK)

    assert len(pieces) > 1
    assert " ".join(pieces) == text
    assert all(piece.strip() for piece in pieces)


def test_each_piece_fits_the_word_budget():
    text = " ".join(
        ["The session middleware validates the refresh token here."] * 3
    )
    pieces = _speech_pieces(text, DEFAULT_PACK)

    assert len(pieces) > 1
    assert all(len(DEFAULT_PACK.word.findall(piece)) <= 18 for piece in pieces)


def test_prepare_samples_removes_dc_and_keeps_headroom():
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    signal = (0.2 + 0.3 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)

    prepared = _prepare_samples(signal, sample_rate, 0.9)

    assert prepared.dtype == np.float32
    assert abs(float(np.mean(prepared))) < 1e-3
    assert float(np.max(np.abs(prepared))) <= 0.98 + 1e-4


def test_prepare_samples_lifts_quiet_speech_to_a_usable_level():
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    # Kokoro lands near this level: audible, but quiet next to other audio.
    signal = (0.3 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)

    prepared = _prepare_samples(signal, sample_rate, 0.9)

    expected = 0.3 * 0.9 * MAKEUP_GAIN
    assert abs(float(np.max(np.abs(prepared))) - expected) < 0.02
    assert float(np.sqrt(np.mean(prepared**2))) > 0.15


def test_prepare_samples_scales_hot_signal_instead_of_clipping():
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    signal = (1.5 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)

    prepared = _prepare_samples(signal, sample_rate, 1.0)

    assert float(np.max(np.abs(prepared))) <= 0.98 + 1e-4
