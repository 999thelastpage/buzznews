"""Generate the raster favicon assets that back favicon.svg.

Draws the Buzznews mark — dark rounded square, off-white serif "B", buzz-red
dot — with Pillow (no SVG rasterizer needed) and writes:
  - web/static/favicon.ico        (16/32/48 multi-size, transparent corners)
  - web/static/apple-touch-icon.png  (180px, full-bleed ink square)

Re-run after editing favicon.svg to keep the raster fallbacks in sync. The
SVG remains the editable source of truth; this is a hand-matched redraw.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

INK = (28, 25, 23, 255)        # #1c1917
PAPER = (244, 240, 232, 255)   # #F4F0E8 (off-white "B")
BUZZ = (154, 58, 58, 255)      # #9a3a3a (buzz-red dot)
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"

STATIC = Path(__file__).resolve().parent.parent / "src" / "buzz_news" / "web" / "static"


def _draw(size: int, rounded: bool) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=INK)
    else:
        d.rectangle([0, 0, size - 1, size - 1], fill=INK)

    font = ImageFont.truetype(FONT_PATH, int(size * 0.66))
    bbox = d.textbbox((0, 0), "B", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) / 2 - bbox[0] - size * 0.05  # nudge left, leave room for dot
    ty = (size - th) / 2 - bbox[1] - size * 0.02
    d.text((tx, ty), "B", font=font, fill=PAPER)

    dr = size * 0.115
    cx, cy = size * 0.74, size * 0.70
    d.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=BUZZ)
    return img


def main() -> None:
    base = _draw(256, rounded=True)
    ico_path = STATIC / "favicon.ico"
    base.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote {ico_path}")

    apple = _draw(180, rounded=False)
    apple_path = STATIC / "apple-touch-icon.png"
    apple.save(apple_path, format="PNG")
    print(f"wrote {apple_path}")


if __name__ == "__main__":
    main()
