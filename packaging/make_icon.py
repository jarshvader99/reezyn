"""Render the Reezyn app icon: the menu-bar seedling on a warm squircle.

The bundle needs an .icns; the menu-bar icon itself is drawn at runtime as a
template image. This script reuses the same sprout geometry at icon scale so
the two marks stay identical.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSMakePoint,
    NSMakeRect,
    NSRectFill,
    NSAffineTransform,
)

# The sprout geometry lives in an 18pt square, as in status_icon.py.
GEOMETRY = 18.0
GEOMETRY_CENTER = (9.0, 8.3)
GEOMETRY_HEIGHT = 10.6
MARGIN = 0.075
CORNER = 0.225
SPROUT_FILL = 0.62

ICON_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def _point(x: float, y: float) -> tuple[float, float]:
    return NSMakePoint(x, y)


def _draw_sprout(scale: float, size: float) -> None:
    transform = NSAffineTransform.transform()
    transform.translateXBy_yBy_(
        size / 2.0 - GEOMETRY_CENTER[0] * scale,
        size / 2.0 - GEOMETRY_CENTER[1] * scale,
    )
    transform.scaleBy_(scale)
    transform.concat()

    stem = NSBezierPath.bezierPath()
    stem.setLineWidth_(1.25)
    stem.setLineCapStyle_(1)  # round
    stem.moveToPoint_(_point(9.0, 3.0))
    stem.curveToPoint_controlPoint1_controlPoint2_(
        _point(9.1, 10.9), _point(8.7, 5.4), _point(9.4, 8.2)
    )
    stem.stroke()

    left = NSBezierPath.bezierPath()
    left.moveToPoint_(_point(9.0, 7.8))
    left.curveToPoint_controlPoint1_controlPoint2_(
        _point(4.1, 12.5), _point(6.8, 7.7), _point(4.2, 9.9)
    )
    left.curveToPoint_controlPoint1_controlPoint2_(
        _point(9.0, 9.2), _point(5.4, 13.6), _point(7.7, 11.3)
    )
    left.closePath()
    left.fill()

    right = NSBezierPath.bezierPath()
    right.moveToPoint_(_point(9.0, 6.3))
    right.curveToPoint_controlPoint1_controlPoint2_(
        _point(13.9, 11.0), _point(11.2, 6.5), _point(13.6, 8.3)
    )
    right.curveToPoint_controlPoint1_controlPoint2_(
        _point(9.0, 7.7), _point(12.6, 11.8), _point(10.8, 9.5)
    )
    right.closePath()
    right.fill()


def render(size: int) -> NSBitmapImageRep:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
    )
    context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(context)
    try:
        NSColor.clearColor().set()
        NSRectFill(NSMakeRect(0, 0, size, size))

        inset = size * MARGIN
        rect = NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
        radius = rect.size.width * CORNER
        background = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, radius, radius
        )
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.965, 0.945, 0.906, 1.0).set()
        background.fill()

        NSColor.colorWithSRGBRed_green_blue_alpha_(0.200, 0.318, 0.231, 1.0).set()
        scale = rect.size.width * SPROUT_FILL / GEOMETRY_HEIGHT
        _draw_sprout(scale, size)
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return rep


def write_png(rep: NSBitmapImageRep, path: Path) -> None:
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    if not data.writeToFile_atomically_(str(path), True):
        raise RuntimeError(f"Could not write {path}")


def main(argv: list[str]) -> int:
    output = Path(argv[1] if len(argv) > 1 else "build/Reezyn.icns")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        iconset = Path(temp) / "Reezyn.iconset"
        iconset.mkdir()
        for name, size in ICON_SIZES:
            write_png(render(size), iconset / name)
        subprocess.run(
            ["iconutil", "--convert", "icns", str(iconset), "--output", str(output)],
            check=True,
        )
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
