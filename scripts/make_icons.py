"""Generates build/icon.png and build/icon.ico (original starburst mark; run: python scripts/make_icons.py)."""
import math
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "build"
OUT.mkdir(exist_ok=True)
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.225), fill=(217, 119, 87, 255))  # terracotta tile
cx = cy = S / 2
for i in range(12):  # 12-ray starburst
    a = math.radians(i * 30 - 90)
    long_ray = i % 2 == 0
    r_out = S * (0.33 if long_ray else 0.25)
    w = S * (0.052 if long_ray else 0.042)
    px, py = -math.sin(a) * w, math.cos(a) * w
    pts = [(cx + px, cy + py), (cx + math.cos(a) * r_out + px * 0.55, cy + math.sin(a) * r_out + py * 0.55),
           (cx + math.cos(a) * r_out - px * 0.55, cy + math.sin(a) * r_out - py * 0.55), (cx - px, cy - py)]
    d.polygon(pts, fill=(250, 249, 245, 255))
d.ellipse((cx - S * 0.055, cy - S * 0.055, cx + S * 0.055, cy + S * 0.055), fill=(250, 249, 245, 255))
img.save(OUT / "icon.png")
img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", OUT / "icon.png", OUT / "icon.ico")
