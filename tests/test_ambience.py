from __future__ import annotations

import numpy as np
import pytest

from reezyn.ambience import DUCK_FACTOR, Ambience
from reezyn.config import AmbienceConfig


def _running_bed(volume: float = 0.35) -> Ambience:
    bed = Ambience(AmbienceConfig(enabled=True, volume=volume))
    bed._gain = bed._target_gain()
    return bed


def test_bed_is_quiet_and_finite():
    bed = _running_bed()
    chunk = bed._render(4800)

    assert chunk.shape == (4800,)
    assert np.isfinite(chunk).all()
    assert float(np.max(np.abs(chunk))) < 0.2
    assert float(np.std(chunk)) > 0.001


def test_bed_fades_in_instead_of_starting_abruptly():
    bed = Ambience(AmbienceConfig(enabled=True, volume=0.35))
    first = bed._render(2400)
    second = bed._render(2400)

    assert float(np.max(np.abs(first))) < 0.05
    assert float(np.max(np.abs(second))) > float(np.max(np.abs(first)))


def test_ducking_lowers_the_target_gain():
    bed = _running_bed(volume=0.5)
    full = bed._target_gain()

    bed.set_ducked(True)

    assert bed._target_gain() == pytest.approx(full * DUCK_FACTOR)
    assert full > bed._target_gain()


def test_start_is_a_noop_when_disabled():
    bed = Ambience(AmbienceConfig(enabled=False))

    assert bed.start() is True
    assert bed.running is False
    bed.stop()
    assert bed.running is False
