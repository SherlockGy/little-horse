# 自检工具：从成片指定时间段等间隔抽帧，拼成胶片条（每格标注时间），用于检查动作连贯性
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "out" / "_strip"


def strip(video, t0, t1, n, out, cols=4, w=480):
    TMP.mkdir(parents=True, exist_ok=True)
    tiles = []
    for i in range(n):
        t = t0 + (t1 - t0) * i / max(1, n - 1)
        p = TMP / f"f{i:02d}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={w}:-1", str(p)], check=True)
        tiles.append((t, Image.open(p).convert("RGB")))
    h = tiles[0][1].height
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), "black")
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(tiles):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + 64, y + 16], fill="black")
        d.text((x + 3, y + 2), f"{t:.2f}s", fill="yellow")
    sheet.save(out)


if __name__ == "__main__":
    video, t0, t1, n, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
    strip(video, t0, t1, n, out)
