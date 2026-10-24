from __future__ import annotations

import pytest

from reezyn.status_icon import (
    BREATH_CYCLE_S,
    BREATH_LOW,
    PAUSED_ALPHA,
    breath_alpha,
    icon_alpha,
    seedling_image,
)


def test_breath_alpha_breathes_slowly_within_bounds():
    samples = [breath_alpha(index / 20) for index in range(int(BREATH_CYCLE_S * 20) + 1)]
    assert min(samples) >= BREATH_LOW - 1e-9
    assert max(samples) <= 1.0 + 1e-9
    assert max(samples) - min(samples) > 0.3
    assert breath_alpha(0.0) == pytest.approx(1.0)
    assert breath_alpha(BREATH_CYCLE_S / 2) == pytest.approx(BREATH_LOW)
    assert breath_alpha(BREATH_CYCLE_S) == pytest.approx(1.0)


def test_icon_alpha_depends_on_the_state():
    assert icon_alpha("listening") == 1.0
    assert icon_alpha("paused") == PAUSED_ALPHA
    assert icon_alpha("speaking", 0.0) == pytest.approx(1.0)
    assert icon_alpha("speaking", BREATH_CYCLE_S / 2) == pytest.approx(BREATH_LOW)


def test_seedling_is_a_cached_template_image():
    image = seedling_image(1.0)
    assert image.isTemplate()
    assert image.size().width == 18.0
    assert image.size().height == 18.0
    assert seedling_image(1.0) is image
    assert seedling_image(0.5) is not image
