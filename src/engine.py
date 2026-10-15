# 纸艺木偶剧渲染引擎：素材加载、摄像机与视差、纸片精灵、粒子、文字与后期
import json
import math
import zlib
from pathlib import Path

import numpy as np
import skia
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
CUT = ROOT / "assets" / "cut"
W, H, FPS = 1920, 1080, 24
SAMP = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kNearest)
# 背景是放大绘制，不需要 mipmap（开启时该版本 skia 极慢）
BG_SAMP = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kNone)
FONT_TF = skia.Typeface.MakeFromFile("C:/Windows/Fonts/STHUPO.TTF")

# 角色缩放：相对原图像素尺寸（按抠图后高度换算到舞台上的目标高度）
SCALE = {"pony": 0.30, "mom": 0.38, "ox": 0.50, "squirrel": 0.165}
# 眨眼帧映射
BLINK = {"pony_sack": "pony_blink", "pony_stand": "pony_nblink", "mom_stand": "mom_blink", "ox_stand": "ox_blink",
         "squirrel_stand": "squirrel_blink"}


# ---------------- 数学与缓动 ----------------
def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def seg(t, t0, t1):
    if t1 <= t0:
        return 1.0 if t >= t0 else 0.0
    return clamp01((t - t0) / (t1 - t0))


def ease_io(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in(x):
    x = clamp01(x)
    return x ** 3


def ease_back(x, s=1.9):
    x = clamp01(x) - 1
    return x * x * ((s + 1) * x + s) + 1


def lerp(a, b, u):
    return a + (b - a) * u


def kf(t, keys, ease=ease_io):
    # 关键帧插值：keys = [(时间, 值), ...]，值可为数字或元组
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            u = ease(seg(t, t0, t1))
            if isinstance(v0, tuple):
                return tuple(lerp(a, b, u) for a, b in zip(v0, v1))
            return lerp(v0, v1, u)
    return keys[-1][1]


def rnd(i, salt=0.0):
    v = math.sin(i * 12.9898 + salt * 78.233 + 0.5) * 43758.5453
    return v - math.floor(v)


def hop(b, b0, b1, x0, x1, rate=1.0, h=26, tilt=4.0):
    # 纸偶跳步行走：返回 (x, 离地高度, 倾斜角, 横向缩放, 纵向缩放)
    if b <= b0:
        return x0, 0.0, 0.0, 1.0, 1.0
    if b >= b1:
        return x1, 0.0, 0.0, 1.0, 1.0
    n = max(1, round((b1 - b0) * rate))
    ph = (b - b0) / (b1 - b0) * n
    k, f = int(ph), ph - int(ph)
    x = x0 + (x1 - x0) * (k + ease_io(f)) / n
    s = math.sin(math.pi * f)
    land = max(0.0, 1 - f / 0.2) if k > 0 else 0.0
    return x, h * s, tilt * math.sin(math.pi * ph), 1 - 0.03 * s + 0.06 * land, 1 + 0.05 * s - 0.07 * land


def jump(b, b0, b1, period=1.0, h=60, duty=1.0):
    # 原地跳：返回 (离地高度, 横向缩放, 纵向缩放)；duty<1 时只在每个周期的前段起跳
    if b < b0 or b > b1:
        return 0.0, 1.0, 1.0
    f = ((b - b0) / period) % 1 / duty
    if f > 1:
        return 0.0, 1.0, 1.0
    s = math.sin(math.pi * f)
    land = max(0.0, 1 - f / 0.18)
    return h * s, 1 - 0.04 * s + 0.08 * land, 1 + 0.07 * s - 0.09 * land


# ---------------- 素材加载 ----------------
def to_skia(arr, mip=True):
    # 转成 skia 原生格式（BGRA 预乘），避免每次绘制都做逐像素格式转换
    a = arr.astype(np.float32)
    al = a[..., 3:4] / 255.0
    bgra = np.dstack([a[..., 2:3] * al, a[..., 1:2] * al, a[..., 0:1] * al, a[..., 3:4]])
    img = skia.Image.fromarray(np.ascontiguousarray(np.round(bgra).astype(np.uint8)), colorType=skia.kBGRA_8888_ColorType,
                               alphaType=skia.kPremul_AlphaType)
    return img.withDefaultMipmaps() if mip else img


def _pil_to_skia(im):
    return to_skia(np.asarray(im.convert("RGBA")))


class Assets:
    def __init__(self):
        self.meta = json.loads((CUT / "meta.json").read_text(encoding="utf-8"))
        self.img, self.k = {}, {}
        ap = CUT / "align.json"
        self.align = json.loads(ap.read_text(encoding="utf-8")) if ap.exists() else {}
        for f in CUT.glob("*.png"):
            n = f.stem
            if n.startswith("_") or n == "paper_texture":
                continue
            im = Image.open(f)
            if n.startswith("bg_"):
                # 背景轻微虚化，模拟浅景深的立体纸模型
                im = im.convert("RGB").filter(ImageFilter.GaussianBlur(1.3))
                self.img[n], self.k[n] = _pil_to_skia(im), 1.0
                continue
            limit = 1000 if n in ("mill", "waterwheel", "fg_water") else 760
            k = 1.0
            if im.height > limit:
                k = im.height / limit
                im = im.resize((round(im.width / k), limit), Image.LANCZOS)
            self.img[n], self.k[n] = _pil_to_skia(im), k
        self._paper()

    def _paper(self):
        # 全画面纸纹：归一化后增强对比，用正片叠底叠加
        tex = Image.open(CUT / "paper_texture.png").convert("RGB").resize((W, H), Image.LANCZOS)
        a = np.asarray(tex).astype(np.float32)
        a = a / a.reshape(-1, 3).mean(0)
        a = np.clip(1 + (a - 1) * 1.1, 0, 1.0) * 255
        # 纸纹 × 暗角，合成为一张 0~256 的乘子图（BGR 顺序），后期用 numpy 一次相乘
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt((xx - 960) ** 2 + (yy - 560) ** 2) / 1250
        vig = 1 - 0.47 * np.clip((r - 0.55) / 0.45, 0, 1) ** 1.5
        m = a / 255 * vig[..., None]
        self.post_mult = np.ascontiguousarray((m[..., ::-1] * 256).astype(np.uint16))

    def size(self, n):
        im = self.img[n]
        return im.width() * self.k[n], im.height() * self.k[n]


# ---------------- 渲染器 ----------------
class Renderer:
    def __init__(self, assets):
        self.A = assets
        self.arr = np.zeros((H, W, 4), np.uint8)
        self.surface = skia.Surface(self.arr, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self.c = self.surface.getCanvas()
        self.frame = 0
        self.cam = (960.0, 540.0, 1.0)
        self.shake = (0.0, 0.0)
        self.tint = None   # 场景整体调色（夜晚等）
        self.base = 1      # 转场裁剪时的画布保存层级
        self.wind = 0.0    # 风力（影响前景草摆动）
        self.dry = False   # 只算位置不画（重叠检查用）
        self.record = True # 是否登记包围盒
        self.boxes = []    # 本帧各元素的屏幕包围盒：(类别, 名称, x0, y0, x1, y1, 幕名)
        self.deferred = [] # 推迟到角色之后再画的飞虫（便于避让角色）
        self.space = 1.0   # 当前图层：视差系数，None 表示屏幕空间
        self.scene_name = ""

    # ---------- 摄像机 ----------
    def begin(self, frame):
        self.frame = frame
        self.cam = (960.0, 540.0, 1.0)
        self.shake = (0.0, 0.0)
        self.tint = None
        self.wind = 0.0
        self.boxes = []
        self.deferred = []
        self.c.restoreToCount(1)
        self.c.clear(skia.Color(30, 24, 20))

    def layer(self, p=1.0):
        # 视差层：p<1 远景移动更慢，p>1 前景移动更快
        c = self.c
        c.restoreToCount(self.base)
        c.save()
        self.space = p
        cx, cy, z = self.cam
        ecx, ecy = cx * p + 960 * (1 - p), cy * p + 540 * (1 - p)
        zp = 1 + (z - 1) * p
        c.translate(960 + self.shake[0] * p, 540 + self.shake[1] * p)
        c.scale(zp, zp)
        c.translate(-ecx, -ecy)

    def screen(self):
        self.c.restoreToCount(self.base)
        self.c.save()
        self.space = None

    def note_box(self, kind, name, x0, y0, x1, y1):
        # 登记元素包围盒（统一换算到屏幕坐标），供重叠检查与飞虫避让
        if not self.record:
            return
        if self.space is not None:
            x0, y0 = self.to_screen(x0, y0, self.space)
            x1, y1 = self.to_screen(x1, y1, self.space)
        self.boxes.append((kind, name, x0, y0, x1, y1, self.scene_name))

    def to_screen(self, x, y, p=1.0):
        cx, cy, z = self.cam
        ecx, ecy = cx * p + 960 * (1 - p), cy * p + 540 * (1 - p)
        zp = 1 + (z - 1) * p
        return (x - ecx) * zp + 960, (y - ecy) * zp + 540

    def _tint_cf(self, amount=1.0):
        if self.tint is None or amount <= 0:
            return None
        m = self.tint
        if amount < 1:
            ident = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0]
            m = [lerp(a, b, amount) for a, b in zip(ident, m)]
        return skia.ColorFilters.Matrix(m)

    # ---------- 背景 ----------
    def bg(self, name, u=0.5, v=0.55, scale=1.3, p=0.85):
        self.layer(p)
        if self.dry:
            return
        im = self.A.img[name]
        bw = W * scale
        bh = bw * im.height() / im.width()
        x0, y0 = 960 - u * bw, 540 - v * bh
        paint = skia.Paint(AntiAlias=True)
        cf = self._tint_cf()
        if cf:
            paint.setColorFilter(cf)
        self.c.drawImageRect(im, skia.Rect.MakeXYWH(x0, y0, bw, bh), BG_SAMP, paint)

    def fg_grass(self, y=1150, h=250, p=1.18, offset=0.0):
        self.flush_fx()
        self.layer(p)
        if self.dry:
            self.layer(1.0)
            return
        im = self.A.img["fg_grass"]
        w = h * im.width() / im.height()
        paint = skia.Paint(AntiAlias=True)
        cf = self._tint_cf()
        if cf:
            paint.setColorFilter(cf)
        paint.setShader(im.makeShader(skia.TileMode.kDecal, skia.TileMode.kDecal, SAMP))
        t = self.frame / FPS
        iw, ih = im.width(), im.height()
        x = -400 + offset
        while x < 2400:
            # 草叶随风摆动：顶部摆幅大、根部固定
            nx, ny = 24, 3
            pos, tex, idx = [], [], []
            for j in range(ny + 1):
                for i in range(nx + 1):
                    u, v = i / nx, j / ny
                    wx = x + u * w
                    sway = (14 * math.sin(t * 1.7 + wx * 0.006) + 6 * math.sin(t * 3.1 + wx * 0.017) + self.wind * 40) * (1 - v) ** 1.5
                    pos.append(skia.Point(wx + sway, y - h + v * h))
                    tex.append(skia.Point(u * iw, v * ih))
            for j in range(ny):
                for i in range(nx):
                    a = j * (nx + 1) + i
                    idx += [a, a + 1, a + nx + 1, a + 1, a + nx + 2, a + nx + 1]
            self.c.drawVertices(skia.Vertices.MakeCopy(skia.Vertices.kTriangles_VertexMode, pos, tex, None, idx), paint)
            x += w - 4
        self.layer(1.0)

    def water(self, y, h=380, p=1.0, flow=36.0, amp=1.0, alpha=1.0, x0=-500, x1=2500):
        # 毛毡河水前景条：整体缓慢横向流动，水面线上下起伏（顶部摆幅大、底部固定）
        self.layer(p)
        if self.dry:
            self.layer(1.0)
            return
        im = self.A.img["fg_water"]
        w = h * im.width() / im.height()
        paint = skia.Paint(AntiAlias=True, Alphaf=alpha)
        cf = self._tint_cf()
        if cf:
            paint.setColorFilter(cf)
        paint.setShader(im.makeShader(skia.TileMode.kDecal, skia.TileMode.kDecal, SAMP))
        t = self.frame / FPS
        iw, ih = im.width(), im.height()
        # 纹理不可平铺：相邻两块左右镜像，接缝处像素一致；按两块为周期滚动，镜像次序不跳变
        x = x0 - (t * flow) % (2 * (w - 4))
        c = self.c
        c.save()
        c.clipRect(skia.Rect.MakeLTRB(x0, -2000, x1, 4000))
        tile = 0
        while x < x1:
            nx, ny = 24, 3
            pos, tex, idx = [], [], []
            for j in range(ny + 1):
                for i in range(nx + 1):
                    u, v = i / nx, j / ny
                    wx = x + u * w
                    dy = amp * (9 * math.sin(t * 1.6 + wx * 0.008) + 4 * math.sin(t * 2.9 + wx * 0.021)) * (1 - v) ** 1.2
                    dx = amp * 5 * math.sin(t * 1.1 + wx * 0.013) * (1 - v)
                    pos.append(skia.Point(wx + dx, y + v * h + dy))
                    tex.append(skia.Point((u if tile % 2 == 0 else 1 - u) * iw, v * ih))
            for j in range(ny):
                for i in range(nx):
                    a = j * (nx + 1) + i
                    idx += [a, a + 1, a + nx + 1, a + 1, a + nx + 2, a + nx + 1]
            c.drawVertices(skia.Vertices.MakeCopy(skia.Vertices.kTriangles_VertexMode, pos, tex, None, idx), paint)
            x += w - 4
            tile += 1
        c.restore()
        self.layer(1.0)

    def ripple(self, x, y, age, life=1.4, r1=130, alpha=0.85, width=5):
        # 水面涟漪：扁椭圆白色纸边圈，向外扩散并淡出
        if age < 0 or age > life:
            return
        u = age / life
        r = r1 * (0.15 + 0.85 * ease_out(u))
        a = alpha * (1 - u) ** 1.3
        c = self.c
        rect = skia.Rect.MakeLTRB(x - r, y - r * 0.24, x + r, y + r * 0.24)
        c.drawOval(rect.makeOffset(0, 4), skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=width,
                                                     Color=skia.Color(40, 90, 130, int(70 * a))))
        c.drawOval(rect, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=width,
                                    Color=skia.Color(255, 255, 255, int(255 * a))))

    # ---------- 纸片精灵 ----------
    def sprite(self, name, x, y, scale=1.0, rot=0.0, sx=1.0, sy=1.0, flip=False, alpha=1.0,
               anchor=(0.5, 1.0), shadow=0.28, cf=None, boil=True, seed=None, tint=1.0, img=None):
        im = img or self.A.img[name]
        k = self.A.k.get(name, 1.0) if img is None else 1.0
        if alpha <= 0.003:
            return
        c = self.c
        if boil:
            # 定格动画的“纸片抖动”：每 3 帧变化一次的微小旋转与位移
            sd = (seed if seed is not None else (zlib.crc32(name.encode()) % 997)) + (self.frame // 3) * 7.31
            rot += (rnd(sd, 1) - 0.5) * 0.7
            x += (rnd(sd, 2) - 0.5) * 1.6
            y += (rnd(sd, 3) - 0.5) * 1.2
        s = scale * k
        fx = -1 if flip else 1
        bw_, bh_ = im.width() * s * abs(sx), im.height() * s * abs(sy)
        self.note_box("sprite", name, x - anchor[0] * bw_, y - anchor[1] * bh_, x + (1 - anchor[0]) * bw_, y + (1 - anchor[1]) * bh_)
        if self.dry:
            return
        tcf = self._tint_cf(tint)
        if cf and tcf:
            cf = skia.ColorFilters.Compose(tcf, cf)
        elif tcf:
            cf = tcf

        def draw(paint):
            c.save()
            c.translate(x, y)
            c.rotate(rot)
            c.scale(sx * s * fx, sy * s)
            c.translate(-anchor[0] * im.width(), -anchor[1] * im.height())
            c.drawImage(im, 0, 0, SAMP, paint)
            c.restore()

        if shadow > 0:
            z = self.cam[2]
            sp = skia.Paint(AntiAlias=True, ImageFilter=skia.ImageFilters.DropShadowOnly(
                7 / (s * z), 9 / (s * z), 6 / (s * z), 6 / (s * z), skia.Color4f(0.18, 0.1, 0.05, shadow * alpha)))
            if flip:
                sp.setImageFilter(skia.ImageFilters.DropShadowOnly(
                    -7 / (s * z), 9 / (s * z), 6 / (s * z), 6 / (s * z), skia.Color4f(0.18, 0.1, 0.05, shadow * alpha)))
            draw(sp)
        paint = skia.Paint(AntiAlias=True, Alphaf=alpha)
        if cf:
            paint.setColorFilter(cf)
        draw(paint)

    def ground_shadow(self, x, y, w, alpha=0.32, lift=0.0):
        k = max(0.25, 1 - lift / 260)
        rw, rh = w * 0.5 * k, w * 0.09 * k
        sh = skia.GradientShader.MakeRadial((0, 0), 1.0, [skia.Color(40, 25, 10, int(255 * alpha * k)), skia.Color(40, 25, 10, 0)])
        c = self.c
        c.save()
        c.translate(x, y)
        c.scale(rw, rh)
        c.drawCircle(0, 0, 1.0, skia.Paint(AntiAlias=True, Shader=sh))
        c.restore()

    def actor(self, pose, x, y, lift=0.0, rot=0.0, sx=1.0, sy=1.0, flip=False, alpha=1.0, cf=None, scale_mul=1.0,
              shadow=True, tint=1.0, bend=0.0, breathe=None, blink=True, t=None):
        # 角色木偶：自动眨眼 + 呼吸起伏 + 身体弯曲（网格变形），动作帧按基准姿势对齐
        ch = pose.split("_")[0]
        s = SCALE[ch] * scale_mul
        base = self.A.align.get(pose, {}).get("base", pose)
        w, h = self.A.size(base)
        tt = self.frame / FPS if t is None else t
        seed = zlib.crc32(ch.encode()) % 101
        if blink and BLINK.get(pose) in self.A.img and self._blinking(tt, seed):
            pose = BLINK[pose]
        if alpha > 0.05:
            al = self.A.align.get(pose)
            fw, fh = self.A.size(pose)
            a_s, a_dx, a_dy = (al["s"], al["dx"], al["dy"]) if al else (1.0, 0.0, 0.0)
            lx0, lx1 = a_dx - w / 2, a_dx + fw * a_s - w / 2
            ly0, ly1 = a_dy - h, a_dy + fh * a_s - h
            if flip:
                lx0, lx1 = -lx1, -lx0
            ss = s
            self.note_box("actor", pose, x + lx0 * ss * sx, y - lift + ly0 * ss * sy, x + lx1 * ss * sx, y - lift + ly1 * ss * sy)
        if self.dry:
            return
        if shadow:
            self.ground_shadow(x, y, w * s * 0.75, 0.3 * alpha, lift)
        br = 0.018 * math.sin(tt * 2 * math.pi / 1.9 + seed) if breathe is None else breathe
        self.puppet(pose, x, y - lift, s, rot, sx, sy, flip, alpha, cf, tint, bend, br, seed)

    def _blinking(self, t, seed):
        period = 2.4 + 1.6 * rnd(seed, 9)
        ph = (t + seed * 0.37) % period
        return ph < 0.13 or (rnd(int((t + seed * 0.37) / period), seed) > 0.7 and 0.3 < ph < 0.43)

    def puppet(self, pose, x, y, s, rot, sx, sy, flip, alpha, cf, tint, bend, breathe, seed):
        if alpha <= 0.003:
            return
        al = self.A.align.get(pose)
        base = al["base"] if al else pose
        im = self.A.img[pose]
        kf_ = self.A.k[pose]
        bw, bh = self.A.size(base)
        # 纸片抖动（定格动画质感）
        sd = seed + (self.frame // 3) * 7.31
        rot += (rnd(sd, 1) - 0.5) * 0.6
        x += (rnd(sd, 2) - 0.5) * 1.4
        # 网格顶点：帧纹理坐标 -> 基准坐标 -> 变形
        nx, ny = 7, 10
        fw, fh = im.width(), im.height()
        a_s, a_dx, a_dy = (al["s"], al["dx"], al["dy"]) if al else (1.0, 0.0, 0.0)
        pos, tex = [], []
        for j in range(ny + 1):
            for i in range(nx + 1):
                tu, tv = fw * i / nx, fh * j / ny
                bx = a_s * tu * kf_ + a_dx
                by = a_s * tv * kf_ + a_dy
                nv = by / bh
                top = max(0.0, 1 - nv)
                bx += bend * bw * top * top
                belly = math.exp(-((nv - 0.62) / 0.22) ** 2)
                bx = bw / 2 + (bx - bw / 2) * (1 + breathe * belly)
                by -= breathe * 0.5 * bh * top * 0.4
                pos.append(skia.Point(bx - bw / 2, by - bh))
                tex.append(skia.Point(tu, tv))
        idx = []
        for j in range(ny):
            for i in range(nx):
                a = j * (nx + 1) + i
                idx += [a, a + 1, a + nx + 1, a + 1, a + nx + 2, a + nx + 1]
        verts = skia.Vertices.MakeCopy(skia.Vertices.kTriangles_VertexMode, pos, tex, None, idx)
        tcf = self._tint_cf(tint)
        if cf and tcf:
            cf = skia.ColorFilters.Compose(tcf, cf)
        elif tcf:
            cf = tcf
        paint = skia.Paint(AntiAlias=True, Shader=im.makeShader(skia.TileMode.kDecal, skia.TileMode.kDecal, SAMP))
        if cf:
            paint.setColorFilter(cf)
        c = self.c
        z = self.cam[2]
        fx = -1 if flip else 1
        c.save()
        c.translate(x, y)
        c.rotate(rot)
        c.scale(sx * s * fx, sy * s)
        k = 1 / (s * z)
        lp = skia.Paint(Alphaf=alpha, ImageFilter=skia.ImageFilters.DropShadow(
            7 * k * fx, 9 * k, 6 * k, 6 * k, skia.Color4f(0.18, 0.1, 0.05, 0.28)))
        m = 0.25 * max(bw, bh)
        c.saveLayer(skia.Rect.MakeLTRB(-bw / 2 - m, -bh - m, bw / 2 + m, m), lp)
        c.drawVertices(verts, paint)
        c.restore()
        c.restore()

    # ---------- 粒子与效果 ----------
    def strands(self, ox, oy, t0, t, n, colors, seed=0, spread=(300, 900), up=500, life=2.0, length=26, width=5,
                gravity=500, wind=500, span=1.0):
        # 稻草/木棍碎屑：无状态粒子，按发射时间计算位置
        c = self.c
        for i in range(n):
            ts = t0 + span * rnd(i, seed)
            age = t - ts
            if age < 0 or age > life:
                continue
            vx = spread[0] + (spread[1] - spread[0]) * rnd(i, seed + 1)
            vy = -up * rnd(i, seed + 2)
            px = ox + (rnd(i, seed + 5) - 0.5) * 160 + vx * age + 0.5 * wind * age * age
            py = oy + (rnd(i, seed + 6) - 0.5) * 160 + vy * age + 0.5 * gravity * age * age
            ang = rnd(i, seed + 3) * 360 + age * 500 * (rnd(i, seed + 4) - 0.5)
            col = colors[i % len(colors)]
            a = 1.0 - seg(age, life * 0.7, life)
            c.save()
            c.translate(px, py)
            c.rotate(ang)
            p = skia.Paint(AntiAlias=True, Color=skia.Color(*col, int(255 * a)))
            c.drawRoundRect(skia.Rect.MakeXYWH(-length / 2, -width / 2, length, width), width / 2, width / 2, p)
            c.restore()

    def leaves(self, ox, oy, t0, t, n, seed=0, life=2.5, speed=900, scale=0.07, span=1.0):
        names = ["leaf1", "leaf2", "leaf3", "leaf4"]
        for i in range(n):
            ts = t0 + span * rnd(i, seed)
            age = t - ts
            if age < 0 or age > life:
                continue
            px = ox + speed * (0.6 + 0.6 * rnd(i, seed + 1)) * age
            py = oy + (rnd(i, seed + 2) - 0.5) * 300 + math.sin(age * 5 + i) * 40 - 120 * age
            self.sprite(names[i % 4], px, py, scale, rot=age * 400 * (rnd(i, seed + 3) - 0.3), anchor=(0.5, 0.5),
                        shadow=0.15, boil=False, sy=0.6 + 0.4 * abs(math.cos(age * 6 + i)))

    def wind_streaks(self, x0, y0, x1, y1, t, n=7, strength=1.0, seed=0):
        # 狼吹出的风：白色纸条般的波浪线从嘴边冲向房子
        c = self.c
        for i in range(n):
            ph = (t * 1.6 * (0.8 + 0.4 * rnd(i, seed)) + i / n) % 1.0
            off = (rnd(i, seed + 1) - 0.5) * 220 * (0.4 + ph)
            seglen = 0.28
            path = skia.Path()
            steps = 14
            for k in range(steps + 1):
                u = ph + (k / steps - 0.5) * seglen
                xx = lerp(x0, x1, u)
                yy = lerp(y0, y1, u) + off * u + math.sin(u * 14 + i + t * 6) * 22 * u
                (path.moveTo if k == 0 else path.lineTo)(xx, yy)
            a = math.sin(math.pi * ph) * 0.85 * strength
            p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeCap=skia.Paint.kRound_Cap,
                           StrokeJoin=skia.Paint.kRound_Join, StrokeWidth=(6 + 6 * ph) * (0.7 + 0.5 * strength),
                           Color=skia.Color(255, 255, 255, int(255 * a)))
            sp = skia.Paint(p)
            sp.setColor(skia.Color(80, 110, 140, int(70 * a)))
            c.save()
            c.translate(4, 6)
            c.drawPath(path, sp)
            c.restore()
            c.drawPath(path, p)

    def puffs(self, x, y, t0, t, n=6, seed=0, life=1.8, rise=160, spread=120, size=0.12, alpha=0.85, drift=40,
              span=0.6, tint=1.0):
        # 纸片云朵做的烟雾/蒸汽/尘土
        clouds = ["cloud1", "cloud2", "cloud3"]
        for i in range(n):
            ts = t0 + span * rnd(i, seed)
            age = t - ts
            if age < 0 or age > life:
                continue
            u = age / life
            px = x + (rnd(i, seed + 1) - 0.5) * spread + drift * u * (1 + rnd(i, seed + 2))
            py = y - rise * ease_out(u) * (0.6 + 0.8 * rnd(i, seed + 3))
            sc = size * (0.5 + 1.0 * ease_out(u)) * (0.7 + 0.6 * rnd(i, seed + 4))
            a = alpha * (1 - ease_in(u)) * min(1, age * 8)
            self.sprite(clouds[i % 3], px, py, sc, rot=(rnd(i, seed + 5) - 0.5) * 40, anchor=(0.5, 0.5), alpha=a,
                        shadow=0.1, boil=False, tint=tint)

    def drops(self, x, y, t0, t, n=24, seed=0, color=(150, 205, 240), life=1.2, speed=700):
        c = self.c
        for i in range(n):
            age = t - t0 - 0.05 * rnd(i, seed)
            if age < 0 or age > life:
                continue
            ang = math.radians(-160 + 140 * rnd(i, seed + 1))
            v = speed * (0.5 + 0.7 * rnd(i, seed + 2))
            px = x + math.cos(ang) * v * age
            py = y + math.sin(ang) * v * age + 0.5 * 1600 * age * age
            r = 7 + 7 * rnd(i, seed + 3)
            a = int(255 * (1 - seg(age, life * 0.6, life)))
            c.drawCircle(px, py, r + 3, skia.Paint(AntiAlias=True, Color=skia.Color(255, 255, 255, a)))
            c.drawCircle(px, py, r, skia.Paint(AntiAlias=True, Color=skia.Color(*color, a)))

    def sweat(self, x, y, t, seed=0):
        c = self.c
        for i in range(3):
            ph = (t * 1.2 + i / 3) % 1
            px = x + (i - 1) * 40 + ph * (i - 1) * 30
            py = y + ph * 90
            a = int(255 * math.sin(math.pi * ph))
            path = skia.Path()
            path.moveTo(px, py - 18)
            path.cubicTo(px + 12, py - 2, px + 12, py + 12, px, py + 12)
            path.cubicTo(px - 12, py + 12, px - 12, py - 2, px, py - 18)
            c.drawPath(path, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=6, Color=skia.Color(255, 255, 255, a)))
            c.drawPath(path, skia.Paint(AntiAlias=True, Color=skia.Color(120, 190, 240, a)))

    def fireflies(self, t, n=18, seed=0, alpha=1.0, area=(0, 200, 1920, 900)):
        c = self.c
        for i in range(n):
            px = area[0] + (area[2] - area[0]) * ((rnd(i, seed) + 0.03 * t * (rnd(i, seed + 1) - 0.5)) % 1)
            py = area[1] + (area[3] - area[1]) * rnd(i, seed + 2) + math.sin(t * 0.9 + i) * 30
            a = alpha * (0.5 + 0.5 * math.sin(t * 2.4 + i * 1.7)) ** 2
            g = skia.GradientShader.MakeRadial((px, py), 22, [skia.Color(255, 240, 150, int(200 * a)), skia.Color(255, 220, 100, 0)])
            c.drawCircle(px, py, 22, skia.Paint(AntiAlias=True, Shader=g))
            c.drawCircle(px, py, 4, skia.Paint(AntiAlias=True, Color=skia.Color(255, 250, 210, int(255 * a))))

    def butterfly(self, name, t, cx, cy, ax=320, ay=90, ph=0.0, scale=0.28):
        x = cx + ax * math.sin(t * 0.55 + ph)
        y = cy + ay * math.sin(t * 1.3 + ph * 2)
        dx = math.cos(t * 0.55 + ph)
        flap = 0.25 + 0.75 * abs(math.cos(t * 11 + ph))
        self.deferred.append(dict(name=name, x=x, y=y, scale=scale, kw=dict(rot=10 * math.sin(t * 2 + ph), sx=flap, flip=dx < 0,
                                  anchor=(0.5, 0.5), shadow=0.18, boil=False), space=self.space))

    def bird(self, t, t0, y, speed=260, scale=0.26, flip=False):
        age = t - t0
        x = -150 + speed * age if not flip else 2070 - speed * age
        if x < -200 or x > 2200:
            return
        self.deferred.append(dict(name="bird", x=x, y=y + math.sin(age * 3) * 20, scale=scale,
                                  kw=dict(sy=0.55 + 0.45 * abs(math.cos(age * 9)), flip=flip, anchor=(0.5, 0.5), shadow=0.15,
                                          boil=False), space=self.space))

    def flush_fx(self):
        # 画出推迟的飞虫：靠近角色时平滑地绕到角色头顶上方，避免纸片互相叠住
        items, self.deferred = self.deferred, []
        actors = [bx for bx in self.boxes if bx[0] in ("actor", "text")]
        for it in items:
            if it["space"] != 1.0 or it["scale"] <= 0.002:
                continue
            x, y = it["x"], it["y"]
            w, h = self.A.size(it["name"])
            hw, hh = w * it["scale"] / 2, h * it["scale"] / 2
            sx_, sy_ = self.to_screen(x, y)
            z = self.cam[2]
            for _, _, x0, y0, x1, y1, _ in actors:
                # 角色包围盒内缩一点（纸片四角是透明的）
                ix = (x1 - x0) * 0.08
                d = max(x0 + ix - (sx_ + hw * z), (sx_ - hw * z) - (x1 - ix), 0.0)
                wgt = 1 - clamp01(d / (90 * z))
                wgt = wgt * wgt * (3 - 2 * wgt)
                top = y0 - (hh + 26) * z
                if wgt > 0 and sy_ > top:
                    sy_ = lerp(sy_, top, wgt)
            cx, cy, z = self.cam
            y = (sy_ - 540) / z + cy
            self.layer(1.0)
            self.sprite(it["name"], x, y, it["scale"], **it["kw"])
            # 已放好的飞虫/星星也算障碍，后面的元素会避开它
            actors.append(("fx", it["name"], sx_ - hw * z, sy_ - hh * z, sx_ + hw * z, sy_ + hh * z, ""))

    # ---------- 文字 ----------
    def paper_text(self, text, x, y, size, fill, rot=0.0, scale=1.0, alpha=1.0, stroke=None, spacing=0.0):
        # 剪纸字：白色纸边 + 彩色纸芯 + 投影，居中绘制
        if alpha <= 0.01 or scale <= 0.01:
            return
        c = self.c
        font = skia.Font(FONT_TF, size)
        wdt = font.measureText(text) + spacing * (len(text) - 1)
        st = stroke if stroke is not None else size * 0.16
        self.note_box("text", text, x - wdt / 2 * scale, y - size * 0.5 * scale, x + wdt / 2 * scale, y + size * 0.5 * scale)
        if self.dry:
            return
        c.save()
        c.translate(x, y)
        c.rotate(rot)
        c.scale(scale, scale)
        base_y = size * 0.36
        a = int(255 * alpha)
        shadow = skia.Paint(AntiAlias=True, Style=skia.Paint.kStrokeAndFill_Style, StrokeWidth=st,
                            StrokeJoin=skia.Paint.kRound_Join, Color=skia.Color(60, 35, 20, int(80 * alpha)),
                            MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, size * 0.05))
        border = skia.Paint(AntiAlias=True, Style=skia.Paint.kStrokeAndFill_Style, StrokeWidth=st,
                            StrokeJoin=skia.Paint.kRound_Join, Color=skia.Color(255, 252, 244, a))
        core = skia.Paint(AntiAlias=True, Color=skia.Color(*fill, a))
        xx = -wdt / 2
        for ch in text:
            cw = font.measureText(ch)
            c.drawString(ch, xx + size * 0.04, base_y + size * 0.06, font, shadow)
            c.drawString(ch, xx, base_y, font, border)
            c.drawString(ch, xx, base_y, font, core)
            xx += cw + spacing
        c.restore()

    def caption(self, text, b, b0, b1, bpm):
        # 绘本式字幕：底部纸条，淡入上滑、淡出
        fade = 0.45 * bpm / 60
        a = min(seg(b, b0, b0 + fade), 1 - seg(b, b1 - fade, b1))
        if a <= 0:
            return
        self.screen()
        c = self.c
        size = 50
        font = skia.Font(FONT_TF, size)
        tw = font.measureText(text)
        y = 1000 + 18 * (1 - ease_out(a))
        if a > 0.3:
            self.note_box("caption", "字幕", 960 - tw / 2 - 46, y - 46, 960 + tw / 2 + 46, y + 30)
        if self.dry:
            return
        rect = skia.Rect.MakeLTRB(960 - tw / 2 - 46, y - 46, 960 + tw / 2 + 46, y + 30)
        c.save()
        c.rotate(-0.4, 960, y)
        sp = skia.Paint(AntiAlias=True, Color=skia.Color(60, 35, 15, int(70 * a)),
                        MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 8))
        c.drawRoundRect(rect.makeOffset(5, 7), 26, 26, sp)
        c.drawRoundRect(rect, 26, 26, skia.Paint(AntiAlias=True, Color=skia.Color(253, 247, 233, int(242 * a))))
        c.drawRoundRect(rect.makeInset(7, 7), 20, 20, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.5,
                                                                Color=skia.Color(225, 196, 160, int(255 * a)),
                                                                PathEffect=skia.DashPathEffect.Make([10, 7], 0)))
        c.drawString(text, 960 - tw / 2, y + 2, font, skia.Paint(AntiAlias=True, Color=skia.Color(105, 66, 42, int(255 * a))))
        c.restore()

    # ---------- 后期 ----------
    def post(self, fade_black=0.0):
        # 后期：纸纹与暗角（numpy 相乘，避免 skia 慢速的全屏混合），可叠加黑场淡入淡出
        self.c.restoreToCount(1)
        if self.dry:
            return
        rgb = self.arr[..., :3]
        mult = self.A.post_mult
        if fade_black > 0:
            k = int(256 * (1 - clamp01(fade_black)))
            rgb[:] = ((rgb.astype(np.uint16) * mult >> 8) * k >> 8).astype(np.uint8)
        else:
            rgb[:] = (rgb.astype(np.uint16) * mult >> 8).astype(np.uint8)

    def torn_clip(self, xedge, seed=0):
        # 撕纸转场的锯齿边：左侧区域
        p = skia.Path()
        p.moveTo(-50, -50)
        p.lineTo(xedge, -50)
        n = 28
        for i in range(n + 1):
            yy = -50 + (H + 100) * i / n
            p.lineTo(xedge + (rnd(i, seed) - 0.5) * 46 + math.sin(i * 1.7) * 10, yy)
        p.lineTo(-50, H + 50)
        p.close()
        return p
