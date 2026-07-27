"""Remove near-black background from coin preview → transparent PNG."""
from pathlib import Path

import numpy as np
from PIL import Image

src = Path(
    r"C:\Users\PEDRO\.grok\sessions\C%3A%5CUsers%5CPEDRO"
    r"\019f683a-7a63-7610-a7d1-ef6c4f006371\images\15.jpg"
)
out_dir = Path(
    r"C:\Users\PEDRO\.gemini\antigravity\scratch\prospector-bot\static\coin-previews"
)
out_dir.mkdir(parents=True, exist_ok=True)

img = Image.open(src).convert("RGBA")
arr = np.array(img).astype(np.float32)
r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
mx = np.maximum(np.maximum(r, g), b)

# Soft alpha from darkness (bg is near black; gold is bright/warm)
alpha = np.clip((mx - 18.0) / (52.0 - 18.0), 0.0, 1.0) * 255.0
warm = (r > g * 0.95) & (r > 35)
alpha = np.where(warm & (mx > 28), np.maximum(alpha, 210), alpha)
# kill pure dark corners hard
alpha = np.where(mx < 16, 0, alpha)

arr[:, :, 3] = alpha.astype(np.uint8)
out = Image.fromarray(arr.astype(np.uint8), "RGBA")

bbox = out.getbbox()
if bbox:
    x0, y0, x1, y1 = bbox
    pad = 16
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(out.width, x1 + pad)
    y1 = min(out.height, y1 + pad)
    out = out.crop((x0, y0, x1, y1))

side = max(out.size) + 16
# even size for cleaner scaling
side = side + (side % 2)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
ox = (side - out.width) // 2
oy = (side - out.height) // 2
canvas.paste(out, (ox, oy), out)

png_path = out_dir / "A13-tilted-nofx-transparent.png"
canvas.save(png_path, "PNG")

# dual preview: dark + light checker so user sees transparency
prev_h = side + 40
prev_w = side * 2 + 48
preview = Image.new("RGB", (prev_w, prev_h), (18, 22, 30))
# left dark
preview.paste((18, 22, 30), [16, 20, 16 + side, 20 + side])
preview.paste(canvas, (16, 20), canvas)
# right light
preview.paste((240, 241, 245), [32 + side, 20, 32 + side * 2, 20 + side])
preview.paste(canvas, (32 + side, 20), canvas)
jpg_path = out_dir / "A13-tilted-nofx-preview.jpg"
preview.save(jpg_path, "JPEG", quality=93)

# also copy raw tilted jpg for reference
Image.open(src).save(out_dir / "A13-tilted-nofx-dark.jpg", "JPEG", quality=93)

print("png", png_path, canvas.size)
print("preview", jpg_path)
