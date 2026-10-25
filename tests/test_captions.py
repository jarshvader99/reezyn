from __future__ import annotations

from reezyn.captions import (
    caption_rows,
    clamp_opacity,
    nearest_font_size,
    panel_frame,
    step_font_size,
)
from reezyn.config import CAPTION_FONTS


def test_the_live_line_follows_the_last_two_thoughts():
    rows = caption_rows("The live line.", ["Second.", "First."])
    assert rows == [("First.", False), ("Second.", False), ("The live line.", True)]


def test_between_thoughts_the_newest_line_is_bright():
    assert caption_rows("", ["Newest.", "Older."]) == [
        ("Newest.", True),
        ("Older.", False),
    ]


def test_a_repeated_line_is_not_shown_twice():
    rows = caption_rows("Live line.", ["Live line.", "Older."])
    assert rows == [("Older.", False), ("Live line.", True)]


def test_whitespace_is_folded_before_display():
    assert caption_rows("  one   line ", []) == [("one line", True)]


def test_a_collapsed_viewer_shows_a_single_line():
    assert caption_rows("Live.", ["Newest.", "Older."], collapsed=True) == [
        ("Live.", True)
    ]
    assert caption_rows("", ["Newest.", "Older."], collapsed=True) == [
        ("Newest.", True)
    ]
    assert caption_rows("", [], collapsed=True) == []


def test_the_panel_is_centred_and_a_third_of_the_screen_wide():
    x, y, width, height = panel_frame(1800.0, 1200.0, 1170.0, 200.0)
    assert width == 600.0
    assert x == 600.0
    assert height == 200.0
    assert y == 1170.0 - 12.0 - 200.0


def test_the_panel_keeps_a_readable_width_on_a_small_screen():
    _, _, width, _ = panel_frame(900.0, 700.0, 680.0, 100.0)
    assert width == 440.0


def test_the_panel_never_grows_past_a_third_of_the_screen():
    _, y, _, height = panel_frame(1800.0, 1200.0, 1170.0, 4000.0)
    assert height == 400.0
    assert y == 1170.0 - 12.0 - 400.0


def test_opacity_is_clamped_and_fonts_snap_to_the_menu_choices():
    assert clamp_opacity(2.0) == 1.0
    assert clamp_opacity(0.1) == 0.3
    assert nearest_font_size(17.0) == CAPTION_FONTS["Small"]
    assert nearest_font_size(23.0) == CAPTION_FONTS["Large"]


def test_font_steps_stop_at_the_ends():
    small = CAPTION_FONTS["Small"]
    large = CAPTION_FONTS["Large"]
    assert step_font_size(small, -1) == small
    assert step_font_size(large, 1) == large
    assert step_font_size(small, 1) == CAPTION_FONTS["Medium"]
    assert step_font_size(large, -1) == CAPTION_FONTS["Medium"]
