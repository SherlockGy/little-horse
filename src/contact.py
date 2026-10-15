# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow"]
# ///
# 生成素材缩略图拼版，便于快速检查
import sys
from pathlib import Path
from PIL import Image, ImageDraw

files = [Path(p) for p in sys.argv[2:]]
out = Path(sys.argv[1])
cell = 360
cols = 4 if len(files) > 3 else len(files)
rows = (len(files) + cols - 1) // cols
sheet = Image.new("RGB", (cols * cell, rows * (cell + 24)), "white")
d = ImageDraw.Draw(sheet)
for i, f in enumerate(files):
    im = Image.open(f).convert("RGBA")
    bg = Image.new("RGBA", im.size, (200, 200, 200, 255))
    im = Image.alpha_composite(bg, im)
    im.thumbnail((cell - 8, cell - 8))
    x, y = (i % cols) * cell, (i // cols) * (cell + 24)
    sheet.paste(im.convert("RGB"), (x + 4, y + 4))
    d.text((x + 6, y + cell), f.stem, fill="black")
sheet.save(out)
