# SPDX-License-Identifier: CC0-1.0

"""Generate Hearthkin.ico — multi-resolution app icon.

Run from the repo root:

    python scripts/generate_icon.py

Output: ../Hearthkin.ico in the repo root, with sizes 16/32/48/64/128/256
embedded so Windows can pick the right one for tray, taskbar, file
explorer, alt-tab, and large-icon views.

Design: warm orange-red circle (the hearth/warmth) with a stylized
flame in the centre. Uses a hand-rolled flame path so we don't
depend on a system font being present. Recognizable at 16×16 (tray
icon) and clean at 256×256 (Windows file-explorer 'large icons').

Requires Pillow (pip install Pillow). Build-time only — not a
runtime dep, not in requirements.txt."""

from pathlib import Path
from PIL import Image, ImageDraw


SIZES = [256, 128, 64, 48, 32, 16]
BG_OUTER = (210, 78, 36, 255)     # deep ember red-orange
BG_INNER = (255, 175, 80, 255)    # warm flame yellow-orange
FLAME_COLOR = (255, 245, 220, 255)  # hot-cream highlight
SHADOW = (120, 30, 10, 60)        # subtle shadow under flame


def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer disc (background hearth) with a one-pixel inset so anti-
    # aliasing doesn't clip on the edges at small sizes.
    pad = max(1, size // 32)
    draw.ellipse([pad, pad, size - pad, size - pad], fill=BG_OUTER)

    # Inner disc (warm core) — slightly smaller than the outer.
    inner_pad = pad + max(1, size // 16)
    draw.ellipse(
        [inner_pad, inner_pad, size - inner_pad, size - inner_pad],
        fill=BG_INNER,
    )

    # Flame: a teardrop pointing up, drawn as a polygon. Coordinates
    # are normalized to the unit square then scaled — keeps the shape
    # consistent across resolutions.
    flame_norm = [
        (0.50, 0.18),  # tip
        (0.62, 0.32),
        (0.66, 0.46),
        (0.62, 0.62),
        (0.55, 0.74),
        (0.58, 0.82),
        (0.50, 0.86),  # bottom
        (0.42, 0.82),
        (0.45, 0.74),
        (0.38, 0.62),
        (0.34, 0.46),
        (0.38, 0.32),
    ]
    flame = [(x * size, y * size) for (x, y) in flame_norm]

    # Soft shadow behind the flame for a touch of depth at larger
    # sizes; skipped at 16×16 because the offset would be invisible.
    if size >= 32:
        shadow_offset = max(1, size // 96)
        shadow = [(x + shadow_offset, y + shadow_offset) for (x, y) in flame]
        draw.polygon(shadow, fill=SHADOW)

    draw.polygon(flame, fill=FLAME_COLOR)

    # Highlight: a smaller flame on the left of the main one to
    # suggest internal heat. Skipped on 16×16 to avoid muddiness.
    if size >= 32:
        highlight_norm = [
            (0.46, 0.36),
            (0.52, 0.42),
            (0.50, 0.52),
            (0.46, 0.60),
            (0.42, 0.52),
            (0.42, 0.42),
        ]
        highlight = [(x * size, y * size) for (x, y) in highlight_norm]
        draw.polygon(highlight, fill=BG_INNER)

    return img


def main():
    out_path = Path(__file__).parent.parent / "Hearthkin.ico"
    images = [make_icon(s) for s in SIZES]
    # PIL writes the .ico with all sizes embedded; Windows picks
    # the closest one for each context. Pass the largest as the base
    # and the rest via the sizes parameter.
    images[0].save(
        out_path,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
    )
    print(f"Wrote {out_path} with sizes {SIZES}.")


if __name__ == "__main__":
    main()
