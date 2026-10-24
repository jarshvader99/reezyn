"""The little seedling in the menu bar.

The icon is drawn at runtime as a template image, so macOS tints it correctly
in light, dark, and highlighted menu bars. Its only motion is a slow breath
while Reezyn is speaking; everything else is still.
"""

from __future__ import annotations

import math
from typing import Any

BREATH_CYCLE_S = 6.0
BREATH_LOW = 0.6
PAUSED_ALPHA = 0.55
ICON_POINTS = 18.0
_RENDER_SCALE = 2.0
_IMAGE_STEPS = 48
_IMAGES: dict[int, Any] = {}


def breath_alpha(elapsed: float) -> float:
    """One slow breath: full at the start, lowest halfway, full again at 6s."""

    phase = (elapsed % BREATH_CYCLE_S) / BREATH_CYCLE_S
    wave = 0.5 + 0.5 * math.cos(2.0 * math.pi * phase)
    return BREATH_LOW + (1.0 - BREATH_LOW) * wave


def icon_alpha(state: str, elapsed: float = 0.0) -> float:
    if state == "speaking":
        return breath_alpha(elapsed)
    if state == "paused":
        return PAUSED_ALPHA
    return 1.0


def reduce_motion() -> bool:
    """Whether macOS is asking apps to avoid non-essential motion."""

    try:
        from AppKit import NSWorkspace
    except ImportError:
        return False
    return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())


def seedling_image(alpha: float = 1.0) -> Any:
    """A crisp template image of a sprout at the requested opacity."""

    key = max(0, min(_IMAGE_STEPS, round(alpha * _IMAGE_STEPS)))
    cached = _IMAGES.get(key)
    if cached is not None:
        return cached

    from AppKit import NSBezierPath, NSColor, NSImage, NSMakePoint, NSMakeSize

    scale = _RENDER_SCALE
    image = NSImage.alloc().initWithSize_(
        NSMakeSize(ICON_POINTS * scale, ICON_POINTS * scale)
    )

    def point(x: float, y: float) -> tuple[float, float]:
        return NSMakePoint(x * scale, y * scale)

    image.lockFocus()
    NSColor.colorWithCalibratedWhite_alpha_(0.0, key / _IMAGE_STEPS).set()

    stem = NSBezierPath.bezierPath()
    stem.setLineWidth_(1.25 * scale)
    stem.setLineCapStyle_(1)  # round
    stem.moveToPoint_(point(9.0, 3.0))
    stem.curveToPoint_controlPoint1_controlPoint2_(
        point(9.1, 10.9), point(8.7, 5.4), point(9.4, 8.2)
    )
    stem.stroke()

    left = NSBezierPath.bezierPath()
    left.moveToPoint_(point(9.0, 7.8))
    left.curveToPoint_controlPoint1_controlPoint2_(
        point(4.1, 12.5), point(6.8, 7.7), point(4.2, 9.9)
    )
    left.curveToPoint_controlPoint1_controlPoint2_(
        point(9.0, 9.2), point(5.4, 13.6), point(7.7, 11.3)
    )
    left.closePath()
    left.fill()

    right = NSBezierPath.bezierPath()
    right.moveToPoint_(point(9.0, 6.3))
    right.curveToPoint_controlPoint1_controlPoint2_(
        point(13.9, 11.0), point(11.2, 6.5), point(13.6, 8.3)
    )
    right.curveToPoint_controlPoint1_controlPoint2_(
        point(9.0, 7.7), point(12.6, 11.8), point(10.8, 9.5)
    )
    right.closePath()
    right.fill()

    image.unlockFocus()
    image.setSize_(NSMakeSize(ICON_POINTS, ICON_POINTS))
    image.setTemplate_(True)
    _IMAGES[key] = image
    return image
