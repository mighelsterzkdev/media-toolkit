"""Draw the application icon (assets/media-toolkit.ico) at every Windows size."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SCALE = 4  # draw large, then downsample for smooth edges


def draw(size):
    canvas = size * SCALE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    unit = canvas / 256
    margin = 8 * unit
    # Dark rounded tile with a faint edge, matching the app's dark theme.
    pen.rounded_rectangle((margin, margin, canvas - margin, canvas - margin), radius=56 * unit, fill=(10, 10, 10, 255),
                          outline=(64, 64, 70, 255), width=max(1, round(4 * unit)))
    # Play triangle resting on a tray: media that is saved.
    pen.rounded_rectangle((70 * unit, 184 * unit, 186 * unit, 200 * unit), radius=8 * unit, fill=(250, 250, 250, 255))
    pen.polygon([(98 * unit, 62 * unit), (98 * unit, 158 * unit), (178 * unit, 110 * unit)], fill=(250, 250, 250, 255))
    # Repeat the second corner so the closing corner is rounded as well.
    pen.line([(98 * unit, 62 * unit), (98 * unit, 158 * unit), (178 * unit, 110 * unit), (98 * unit, 62 * unit), (98 * unit, 158 * unit)],
             fill=(250, 250, 250, 255), width=round(14 * unit), joint="curve")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    output = ROOT / "assets" / "media-toolkit.ico"
    output.parent.mkdir(exist_ok=True)
    images = [draw(size) for size in SIZES]
    images[-1].save(output, sizes=[(size, size) for size in SIZES], append_images=images[:-1])
    images[-1].save(output.with_suffix(".png"))
    print(output)


if __name__ == "__main__":
    main()
