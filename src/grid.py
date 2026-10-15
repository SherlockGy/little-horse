# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow"]
# ///
# 把多张静帧拼成 2 列网格（每格 960x540）便于检查
import sys
from PIL import Image, ImageDraw
out, files = sys.argv[1], sys.argv[2:]
rows = (len(files) + 1) // 2
g = Image.new("RGB", (1920, 540 * rows), "black")
d = ImageDraw.Draw(g)
for i, f in enumerate(files):
    im = Image.open(f).convert("RGB").resize((960, 540), Image.LANCZOS)
    g.paste(im, ((i % 2) * 960, (i // 2) * 540))
    d.text(((i % 2) * 960 + 8, (i // 2) * 540 + 6), f.split("still_")[-1], fill="white")
g.save(out)
