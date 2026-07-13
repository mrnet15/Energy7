#!/usr/bin/env python3
"""Generate Energy 7's logo assets: energy7.ico and energy7.png (neon burst + bolt)."""
import colorsys
import math
from PIL import Image, ImageDraw, ImageFilter


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def make_logo(size=512):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2

    # Dark radial-ish background panel.
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22),
                        fill=(14, 16, 26, 255))
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=int(size * 0.21),
                        outline=(40, 46, 70, 255), width=max(2, size // 160))

    # Neon kaleidoscope burst.
    spokes = 40
    rmax = size * 0.46
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i in range(spokes):
        ang = (i / spokes) * 2 * math.pi
        hue = (i / spokes) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
        col = (int(r * 255), int(g * 255), int(b * 255), 230)
        length = rmax * (0.55 + 0.45 * abs(math.sin(i * 1.7)))
        x2 = cx + math.cos(ang) * length
        y2 = cy + math.sin(ang) * length
        gd.line([cx, cy, x2, y2], fill=col, width=max(2, size // 130))
        tr = size * 0.012
        gd.ellipse([x2 - tr, y2 - tr, x2 + tr, y2 + tr], fill=col)
    glow_blur = glow.filter(ImageFilter.GaussianBlur(size // 90))
    img = Image.alpha_composite(img, glow_blur)
    img = Image.alpha_composite(img, glow)
    d = ImageDraw.Draw(img)

    # Central dark disc to seat the bolt.
    rr = size * 0.30
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    dd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(10, 12, 20, 235))
    dd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(0, 229, 255, 255),
               width=max(2, size // 120))
    img = Image.alpha_composite(img, disc)

    # Lightning bolt (energy!).
    s = size
    bolt = [
        (0.56, 0.20), (0.40, 0.54), (0.50, 0.54),
        (0.44, 0.80), (0.66, 0.44), (0.54, 0.44), (0.62, 0.20),
    ]
    pts = [(cx + (x - 0.5) * s, cy + (y - 0.5) * s) for x, y in bolt]
    boltimg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(boltimg)
    bd.polygon(pts, fill=(255, 255, 255, 255))
    bd.line(pts + [pts[0]], fill=(0, 229, 255, 255), width=max(2, size // 150),
            joint="curve")
    boltglow = boltimg.filter(ImageFilter.GaussianBlur(size // 70))
    img = Image.alpha_composite(img, boltglow)
    img = Image.alpha_composite(img, boltimg)

    # Round the corners cleanly.
    img.putalpha(rounded_mask(size, int(size * 0.22)))
    return img


def main():
    big = make_logo(512)
    big.save("energy7.png")
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    big.save("energy7.ico", sizes=sizes)
    print("wrote energy7.png and energy7.ico")


if __name__ == "__main__":
    main()
