"""Export coin image 15 → transparent PNG asset for the app."""
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(
    r"C:\Users\PEDRO\.grok\sessions\C%3A%5CUsers%5CPEDRO"
    r"\019f683a-7a63-7610-a7d1-ef6c4f006371\images\15.jpg"
)
STATIC = Path(
    r"C:\Users\PEDRO\.gemini\antigravity\scratch\prospector-bot\static"
)

img = Image.open(SRC).convert("RGBA")
arr = np.array(img).astype(np.float32)
r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
mx = np.maximum(np.maximum(r, g), b)

alpha = np.clip((mx - 16.0) / (48.0 - 16.0), 0.0, 1.0) * 255.0
warm = (r > g * 0.92) & (r > 32)
alpha = np.where(warm & (mx > 26), np.maximum(alpha, 220), alpha)
alpha = np.where(mx < 14, 0, alpha)
arr[:, :, 3] = alpha.astype(np.uint8)
out = Image.fromarray(arr.astype(np.uint8), "RGBA")

bbox = out.getbbox()
if bbox:
    x0, y0, x1, y1 = bbox
    pad = 20
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(out.width, x1 + pad), min(out.height, y1 + pad)
    out = out.crop((x0, y0, x1, y1))

# square transparent canvas
side = max(out.size) + 24
side += side % 2
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(out, ((side - out.width) // 2, (side - out.height) // 2), out)

# main asset 512
main = canvas.resize((512, 512), Image.Resampling.LANCZOS)
main_path = STATIC / "trovoeda-coin.png"
main.save(main_path, "PNG", optimize=True)

# also 128 for tiny icons (same file works via browser scale)
print("saved", main_path, main.size, "mode", main.mode)
