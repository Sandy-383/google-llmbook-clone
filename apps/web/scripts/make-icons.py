"""Draws LLMBook's app icons (an open book on indigo) into public/icons and src/app/favicon.ico.

    uv run --project ../api python scripts/make-icons.py      # from apps/web

Drawn at 4x and scaled down for smooth edges. "Maskable" icons keep the book inside the
central safe zone, because Android crops them to circles, squircles and so on.
"""

from pathlib import Path

from PIL import Image, ImageDraw

INDIGO = (79, 70, 229, 255)
WHITE = (255, 255, 255, 255)
WEB = Path(__file__).resolve().parent.parent


def book(size: int, *, scale: float, rounded: bool) -> Image.Image:
    big = size * 4
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(big * 0.22) if rounded else 0
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=radius, fill=INDIGO)

    def p(x: float, y: float) -> tuple[float, float]:
        # Coordinates in a unit square, scaled about the centre.
        return (big * (0.5 + (x - 0.5) * scale), big * (0.5 + (y - 0.5) * scale))

    gap = 0.025
    left = [p(0.14, 0.24), p(0.5 - gap, 0.31), p(0.5 - gap, 0.80), p(0.14, 0.73)]
    right = [p(0.86, 0.24), p(0.5 + gap, 0.31), p(0.5 + gap, 0.80), p(0.86, 0.73)]
    draw.polygon(left, fill=WHITE)
    draw.polygon(right, fill=WHITE)

    # Lines of text on each page.
    width = max(1, int(big * 0.022 * scale))
    for i in range(4):
        y0 = 0.37 + i * 0.085
        draw.line([p(0.21, y0), p(0.42, y0 + 0.04)], fill=INDIGO, width=width)
        draw.line([p(0.79, y0), p(0.58, y0 + 0.04)], fill=INDIGO, width=width)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    icons = WEB / "public" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        book(size, scale=1.0, rounded=True).save(icons / f"icon-{size}.png", optimize=True)
        book(size, scale=0.72, rounded=False).save(icons / f"maskable-{size}.png", optimize=True)
    # iOS rounds the corners itself.
    book(180, scale=0.9, rounded=False).save(icons / "apple-touch-icon.png", optimize=True)
    book(256, scale=1.0, rounded=True).save(
        WEB / "src" / "app" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)]
    )
    print("icons written to", icons)


if __name__ == "__main__":
    main()
