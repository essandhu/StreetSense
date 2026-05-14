"""Build the 5 fixture images used by the perception unit tests.

These are *synthetic* 128x128 PNGs, generated deterministically with
Pillow so re-running the script produces byte-identical files. They
intentionally cover a few obvious cases the perception scorer should
distinguish:

- ``01_obvious_lane_markings.png`` — black asphalt, two solid white
  lane stripes. Clear lane markings present.
- ``02_faded_lane_markings.png`` — same layout, low-contrast / faded
  stripes.
- ``03_no_lane_markings.png`` — uniform asphalt; no stripes.
- ``04_obstructed.png`` — asphalt with a large opaque rectangle
  covering most of the road (occlusion).
- ``05_lighting_edge_case.png`` — strong shadow band across the road,
  partially obscuring stripes.

These are deliberately hand-built so the perception scorer's outputs
are *interpretable* without needing real-world ground truth. Per ADR
0004, this set doubles as the relaxed synthetic validation set for
the model-selection benchmark (Task 3.3.6).

Run with:

    uv run python tools/perception/build_fixture_images.py

Outputs land under ``tests/fixtures/perception/images/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

_W = _H = 128

# 6-pixel-wide road takes most of the frame; everything is grayscale.
_ASPHALT = (60, 60, 60)
_ASPHALT_LIGHTER = (95, 95, 95)
_LANE_BRIGHT = (240, 240, 240)
_LANE_FADED = (130, 130, 130)
_SHADOW = (30, 30, 30, 180)
_OBSTRUCTION = (200, 90, 30)


def _new_road() -> Image.Image:
    img = Image.new("RGB", (_W, _H), _ASPHALT)
    draw = ImageDraw.Draw(img)
    # Slight road-edge gradient — strip down each side that's a bit
    # lighter, to give the model something more than uniform color.
    draw.rectangle((0, 0, 18, _H), fill=_ASPHALT_LIGHTER)
    draw.rectangle((_W - 18, 0, _W, _H), fill=_ASPHALT_LIGHTER)
    return img


def _draw_stripes(img: Image.Image, color: tuple[int, int, int]) -> None:
    draw = ImageDraw.Draw(img)
    # Two solid lane stripes running vertically — dashed pattern.
    for y in range(8, _H - 8, 24):
        draw.rectangle((38, y, 44, y + 16), fill=color)
        draw.rectangle((_W - 44, y, _W - 38, y + 16), fill=color)


def build_obvious(path: Path) -> None:
    img = _new_road()
    _draw_stripes(img, _LANE_BRIGHT)
    img.save(path, "PNG", optimize=True)


def build_faded(path: Path) -> None:
    img = _new_road()
    _draw_stripes(img, _LANE_FADED)
    img.save(path, "PNG", optimize=True)


def build_none(path: Path) -> None:
    img = _new_road()
    img.save(path, "PNG", optimize=True)


def build_obstructed(path: Path) -> None:
    img = _new_road()
    _draw_stripes(img, _LANE_BRIGHT)
    overlay = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle((20, 40, _W - 20, _H - 20), fill=(*_OBSTRUCTION, 235))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.save(path, "PNG", optimize=True)


def build_shadow(path: Path) -> None:
    img = _new_road()
    _draw_stripes(img, _LANE_BRIGHT)
    overlay = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle((0, 50, _W, 95), fill=_SHADOW)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.save(path, "PNG", optimize=True)


_BUILDERS = {
    "01_obvious_lane_markings.png": build_obvious,
    "02_faded_lane_markings.png": build_faded,
    "03_no_lane_markings.png": build_none,
    "04_obstructed.png": build_obstructed,
    "05_lighting_edge_case.png": build_shadow,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="tests/fixtures/perception/images",
        help="Directory to write PNGs (relative to repo root).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    out_dir = repo_root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, builder in _BUILDERS.items():
        builder(out_dir / name)
        print(f"Wrote {(out_dir / name).relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
