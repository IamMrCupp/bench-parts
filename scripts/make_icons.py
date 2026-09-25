"""Draw the app icon and write it in every format the packages need.

    uv run python scripts/make_icons.py

A chip on benchhud's teal: parts.png (1024), parts.icns (macOS), parts.ico
(Windows) and parts-<size>.png (Linux), all in packaging/icons/. Pillow is
already a dev dependency (the label bridge), so this adds nothing.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "packaging" / "icons"
TEAL = (79, 151, 168, 255)  # benchhud's border colour, so the apps read as a family
INK = (244, 251, 252, 255)


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.rounded_rectangle((64 * s, 64 * s, 960 * s, 960 * s), radius=200 * s, fill=TEAL)
    # The chip: a body with three pins on each side.
    w = round(44 * s)
    d.rounded_rectangle((336 * s, 336 * s, 688 * s, 688 * s), radius=40 * s, outline=INK, width=w)
    for c in (416, 512, 608):
        d.line((c * s, 208 * s, c * s, 336 * s), fill=INK, width=w)
        d.line((c * s, 688 * s, c * s, 816 * s), fill=INK, width=w)
        d.line((208 * s, c * s, 336 * s, c * s), fill=INK, width=w)
        d.line((688 * s, c * s, 816 * s, c * s), fill=INK, width=w)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.save(OUT / "parts.png")
    big.save(OUT / "parts.icns")
    big.save(OUT / "parts.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    for s in (16, 32, 64, 128, 256, 512):
        big.resize((s, s), Image.LANCZOS).save(OUT / f"parts-{s}.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
