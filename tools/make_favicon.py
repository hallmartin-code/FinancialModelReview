"""Render the TEN Capital mark to PNG favicons.

Committed so the assets are reproducible: run ``python tools/make_favicon.py``
to regenerate everything in ``src/deckscan/static/``.

The mark is three arcs around a ring — coral, amber, teal — each with a dot
sitting inside it, reading as three figures holding a circle. Everything is
drawn on a 1024px master and downsampled with LANCZOS, because PIL's drawing
primitives are not anti-aliased and a 32px favicon drawn directly looks ragged.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).resolve().parent.parent / "src" / "deckscan" / "static"

MASTER = 1024

# Design tokens, identical to the web UI's palette in base.html.
CORAL = "#EE5A4E"
AMBER = "#F3A22A"
TEAL = "#35BEBB"

#: Proportions of the master canvas, measured off the source logo.
OUTER_R = 0.480
RING_W = 0.145
DOT_R = 0.098
DOT_ORBIT = 0.235

#: Visual span of each arc *including* its round caps, so the three gaps read
#: as clear slots (the slot reads wider at the rim than at the midline). The
#: caps add ~10° per end at this stroke width; the drawn span compensates.
ARC_SPAN = 117.0

#: (colour, centre angle in PIL degrees — 0° is east, growing clockwise)
FIGURES = ((CORAL, 240.0), (AMBER, 0.0), (TEAL, 120.0))

#: Sizes to emit. 180 is apple-touch-icon, 192 is the PWA/Android icon.
SIZES = {
    "favicon.png": 32,
    "favicon-16.png": 16,
    "favicon-48.png": 48,
    "apple-touch-icon.png": 180,
    "favicon-192.png": 192,
    "favicon-512.png": 512,
}


def _point(centre: float, radius: float, degrees: float) -> tuple[float, float]:
    radians = math.radians(degrees)
    return centre + radius * math.cos(radians), centre + radius * math.sin(radians)


def draw_mark(size: int = MASTER) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    centre = size / 2
    outer = size * OUTER_R
    width = size * RING_W
    midline = outer - width / 2  # PIL strokes inward from the bounding box
    dot_r = size * DOT_R
    orbit = size * DOT_ORBIT

    box = (centre - outer, centre - outer, centre + outer, centre + outer)

    # A round cap extends the arc past its endpoint by half the stroke width;
    # subtract that so the visible gaps stay open.
    cap = math.degrees(math.atan2(width / 2, midline))

    for colour, middle in FIGURES:
        half = ARC_SPAN / 2 - cap
        start, end = middle - half, middle + half
        draw.arc(box, start=start, end=end, fill=colour, width=round(width))

        # Round the arc ends: PIL draws butt caps, the logo has round ones.
        for angle in (start, end):
            x, y = _point(centre, midline, angle)
            draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=colour)

        x, y = _point(centre, orbit, middle)
        draw.ellipse((x - dot_r, y - dot_r, x + dot_r, y + dot_r), fill=colour)

    return image


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    master = draw_mark()
    for name, size in sorted(SIZES.items(), key=lambda item: -item[1]):
        master.resize((size, size), Image.LANCZOS).save(STATIC / name, optimize=True)
        print(f"{name:<22} {size:>4}px")


if __name__ == "__main__":
    main()
