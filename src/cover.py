# 封面生成：用片中同一套纸艺素材合成横版（16:9）与竖版（9:16）封面
import math
import sys
from pathlib import Path

import numpy as np
import skia
from PIL import Image

from engine import BG_SAMP, CUT, FONT_TF, Assets, Renderer, SAMP

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
LOG = "[cover 封面生成]"
TITLE_COLORS = [(236, 122, 98), (242, 186, 72), (98, 168, 222), (120, 186, 120)]


class CoverCanvas:
    # 任意尺寸的画布，借用渲染引擎的角色、精灵、剪纸字绘制方法
    def __init__(self, R, w, h):
        self.R, self.w, self.h = R, w, h
        self.arr = np.zeros((h, w, 4), np.uint8)
        self.surface = skia.Surface(self.arr, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        R.c = self.surface.getCanvas()
        R.frame = 5          # 固定帧号：纸片抖动取一个稳定值
        R.tint = None
        R.cam = (960.0, 540.0, 1.0)
        R.c.clear(skia.Color(30, 24, 20))

    def bg(self, name, scale_w, cx_u, cy_v):
        # 背景按宽度缩放，把图上的 (cx_u, cy_v) 对准画布中心
        im = self.R.A.img[name]
        bw = scale_w
        bh = bw * im.height() / im.width()
        x0, y0 = self.w / 2 - cx_u * bw, self.h / 2 - cy_v * bh
        self.R.c.drawImageRect(im, skia.Rect.MakeXYWH(x0, y0, bw, bh), BG_SAMP, skia.Paint(AntiAlias=True))

    def grass(self, y, h):
        # 前景草丛条：横向平铺，底部贴边
        im = self.R.A.img["fg_grass"]
        w = h * im.width() / im.height()
        x = -60
        while x < self.w:
            self.R.c.drawImageRect(im, skia.Rect.MakeXYWH(x, y, w, h), SAMP, skia.Paint(AntiAlias=True))
            x += w - 4

    def banner(self, text, cx, cy, size):
        # 纸条横幅：米白纸底 + 虚线针脚 + 投影
        c = self.R.c
        font = skia.Font(FONT_TF, size)
        tw = font.measureText(text)
        rect = skia.Rect.MakeLTRB(cx - tw / 2 - size * 0.8, cy - size * 0.95, cx + tw / 2 + size * 0.8, cy + size * 0.6)
        c.save()
        c.rotate(-1.2, cx, cy)
        c.drawRoundRect(rect.makeOffset(8, 10), size * 0.5, size * 0.5,
                        skia.Paint(AntiAlias=True, Color=skia.Color(60, 35, 15, 80),
                                   MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 10)))
        c.drawRoundRect(rect, size * 0.5, size * 0.5, skia.Paint(AntiAlias=True, Color=skia.Color(253, 247, 233)))
        c.drawRoundRect(rect.makeInset(size * 0.15, size * 0.15), size * 0.38, size * 0.38,
                        skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=size * 0.05,
                                   Color=skia.Color(225, 196, 160), PathEffect=skia.DashPathEffect.Make([size * 0.2, size * 0.14], 0)))
        c.drawString(text, cx - tw / 2, cy + size * 0.02, font, skia.Paint(AntiAlias=True, Color=skia.Color(105, 66, 42)))
        c.restore()

    def title(self, cx, cy, size, gap):
        for i, ch in enumerate("小马过河"):
            self.R.paper_text(ch, cx + (i - 1.5) * gap, cy + [6, -8, 4, -6][i], size, TITLE_COLORS[i], stroke=size * 0.11,
                              rot=[-6, 4, -4, 6][i])

    def qmark(self, x, y, size, ch="？", color=(98, 168, 222), rot=10):
        self.R.paper_text(ch, x, y, size, color, rot=rot)

    def finish(self, path):
        # 后期：纸纹 × 暗角，与成片一致
        tex = Image.open(CUT / "paper_texture.png").convert("RGB").resize((self.w, self.h), Image.LANCZOS)
        a = np.asarray(tex).astype(np.float32)
        a = a / a.reshape(-1, 3).mean(0)
        a = np.clip(1 + (a - 1) * 1.1, 0, 1.0)
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        r = np.sqrt((xx - self.w / 2) ** 2 + (yy - self.h * 0.52) ** 2) / (0.65 * math.hypot(self.w, self.h))
        vig = 1 - 0.42 * np.clip((r - 0.55) / 0.45, 0, 1) ** 1.5
        m = a * vig[..., None]
        rgb = self.arr[..., :3].astype(np.float32) * m[..., ::-1]
        self.arr[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        im = Image.fromarray(self.arr[..., [2, 1, 0]].copy())
        im.save(path.with_suffix(".png"))
        im.save(path.with_suffix(".jpg"), quality=93)
        print(f"{LOG} 输出 {path.with_suffix('.png')} / .jpg", flush=True)


def horizontal(R):
    C = CoverCanvas(R, 1920, 1080)
    C.bg("bg_river", 1920 * 1.32, 0.56, 0.47)
    # 老牛（左）、小马（中）、松鼠（右），彼此留出空隙
    R.actor("ox_laugh", 330, 930, scale_mul=1.05, blink=False)
    R.actor("pony_ask", 1000, 1000, scale_mul=1.75, blink=False)
    R.actor("squirrel_warn", 1610, 960, flip=True, scale_mul=1.45, blink=False)
    C.qmark(1380, 470, 150)
    C.qmark(1470, 560, 100, rot=-8)
    C.grass(1080 - 230, 260)
    C.title(960, 190, 210, 225)
    C.banner("小马能过河吗？", 960, 395, 64)
    C.finish(OUT / "cover_16x9")


def vertical(R):
    C = CoverCanvas(R, 1080, 1920)
    C.bg("bg_river", 1920 * 1.62, 0.62, 0.50)
    R.actor("ox_laugh", 285, 1040, scale_mul=0.78, blink=False)
    R.actor("squirrel_warn", 860, 1040, flip=True, scale_mul=1.25, blink=False)
    R.actor("pony_ask", 540, 1760, scale_mul=2.25, blink=False)
    C.qmark(690, 905, 130)
    C.qmark(600, 975, 86, rot=-8)
    C.grass(1920 - 250, 280)
    C.title(540, 300, 215, 228)
    C.banner("小马能过河吗？", 540, 520, 66)
    C.finish(OUT / "cover_9x16")


def main():
    R = Renderer(Assets())
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "h"):
        horizontal(R)
    if which in ("all", "v"):
        vertical(R)


if __name__ == "__main__":
    main()
