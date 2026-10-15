# 小马过河：分镜与动作编排（所有动作以“幕内拍号”计时，与配乐、旁白同步）
import math
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import skia

from engine import (FPS, H, W, Assets, Renderer, SCALE, clamp01, ease_back, ease_in, ease_io, ease_out, jump, kf,
                    lerp, rnd, seg)
from timeline import CUES, SCENES, TL, TOTAL, voice_spans

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
ORDER = [s[0] for s in SCENES]
TITLE_COLORS = [(236, 122, 98), (242, 186, 72), (98, 168, 222), (120, 186, 120)]
GY = 905             # 角色脚底所在的地面高度
SPANS = voice_spans()

# 动作循环：(迈步帧A, 迈步帧B, 落地帧)
TROT = ("pony_walka", "pony_walkb", "pony_sack")
NTROT = ("pony_nwalka", "pony_nwalkb", "pony_stand")
GALLOP = ("pony_gallop", "pony_gallop", "pony_walka")

# 各背景的取景参数 (u, v, scale)
BG_RIVER = dict(u=0.55, v=0.5, scale=1.3)
BG_HOME = dict(u=0.45, v=0.5, scale=1.3)
OXX = 330            # 老牛在河岸上的位置
EDGE = 1380          # 河岸边（再往右就是河水）
MOMX = 560           # 马妈妈在马棚前的位置
NUZZLE_X = 905       # 蹭蹭头时小马凑近到的位置（与妈妈低头后的脸刚好贴上）


# ---------------- 通用小工具 ----------------
def act_pt(R, pose, x, y, u, v, flip=False, scale_mul=1.0):
    # 角色图内归一化坐标 -> 世界坐标
    w, h = R.A.size(pose)
    s = SCALE[pose.split("_")[0]] * scale_mul
    return x + (u - 0.5) * w * s * (-1 if flip else 1), y - (1 - v) * h * s


def pick(R, name, fallback):
    # 动作帧存在才使用，否则退回原姿势
    return name if name in R.A.img else fallback


def bg_point(R, u, v, uc, vc, scale, p=0.85):
    # 背景图上的点（考虑背景视差层）换算到前景世界坐标，镜头推拉时特效也能和背景对齐
    bw = W * scale
    bh = bw * 1024 / 1536
    sx, sy = R.to_screen(960 + (u - uc) * bw, 540 + (v - vc) * bh, p)
    cx, cy, z = R.cam
    return (sx - 960) / z + cx, (sy - 540) / z + cy


def set_cam(R, cam):
    # 手持摄影机的轻微漂移，避免画面完全静止
    t = R.frame / FPS
    cx, cy, z = cam
    R.cam = (cx + 6 * math.sin(t * 0.43) + 3 * math.sin(t * 1.13), cy + 4 * math.sin(t * 0.57 + 1), z * (1 + 0.005 * math.sin(t * 0.31)))


def clouds(R, t, ys=(70, 130, 40), speed=14):
    R.layer(0.9)
    for i, (n, y) in enumerate(zip(["cloud1", "cloud2", "cloud3"], ys)):
        x = (300 + i * 760 + speed * (1 + 0.4 * i) * t) % 2700 - 400
        R.sprite(n, x, y, 0.4 - 0.06 * i, anchor=(0.5, 0.5), shadow=0.12, boil=False, alpha=0.96)
    R.layer(1.0)


def talking(t, seed=0.0):
    # 说话姿势的切换节奏：说话时大部分时间保持说话帧，只偶尔、缓慢地换回原姿势（1.5~3 秒一次），避免频繁点头
    return math.sin(t * 2.3 + seed) + 0.5 * math.sin(t * 0.9 + 1 + seed) > -0.45


def in_span(b, spans):
    return any(b0 <= b < b1 for b0, b1 in spans)


def sprite_h(R, name, height):
    # 让道具在舞台上达到指定高度的缩放
    return height / R.A.size(name)[1]


# ---------------- 角色动作 ----------------
def walker(R, cyc, b, b0, b1, x0, x1, rate=1.0, h=26, y=GY, flip=None, tilt=3.0, lean=0.05, dust=True, yfun=None,
           step_cb=None, **kw):
    # 逐帧步行/奔跑：迈步帧交替 + 落地压扁 + 扬尘；yfun(x) 可给出随位置变化的脚底高度（涉水）
    a, bb, contact = cyc
    if flip is None:
        flip = x1 < x0
    yf = yfun or (lambda _x: y)
    if b <= b0 or b >= b1:
        x = x0 if b <= b0 else x1
        R.actor(contact, x, yf(x), flip=flip, **kw)
        return x
    n = max(1, round((b1 - b0) * rate))
    ph = (b - b0) / (b1 - b0) * n
    k, f = int(ph), ph - int(ph)
    u = (k + ease_io(f)) / n
    x = lerp(x0, x1, u)
    s = math.sin(math.pi * f)
    land = max(0.0, 1 - f / 0.18) if k > 0 else 0.0
    pose = (a if k % 2 == 0 else bb) if 0.08 < f < 0.92 else contact
    R.actor(pose, x, yf(x), h * s, rot=tilt * math.sin(math.pi * ph) * 0.6 * (-1 if flip else 1),
            sx=1 - 0.03 * s + 0.06 * land, sy=1 + 0.05 * s - 0.07 * land, flip=flip, bend=lean, **kw)
    for j in (k, k - 1):
        if j < 1:
            continue
        tb = b0 + j * (b1 - b0) / n
        xj = lerp(x0, x1, j / n)
        if step_cb:
            step_cb(xj, tb, j)
        elif dust:
            R.puffs(xj, yf(xj) + 4, tb * R.spb, b * R.spb, n=3, seed=j * 7 + int(x0) % 97, life=0.55, rise=26,
                    spread=70, size=0.05, drift=-30 if not flip else 30, span=0.04, alpha=0.6)
    return x


def turn(R, pose, x, b, b0, b1, from_flip, y=GY, **kw):
    # 纸片转身：横向压扁到一条线再翻面
    u = seg(b, b0, b1)
    R.actor(pose, x, y, 10 * math.sin(math.pi * u), sx=max(0.08, abs(1 - 2 * u)),
            flip=from_flip if u < 0.5 else not from_flip, **kw)


def hopper(R, air, ground, b, b0, b1, x, period=1.0, h=50, y=GY, flip=False, dust=True, **kw):
    # 原地蹦跳：起跳拉伸、落地压扁
    if b < b0 or b >= b1:
        R.actor(ground, x, y, flip=flip, **kw)
        return
    f = ((b - b0) / period) % 1
    if f < 0.15:
        e = math.sin(math.pi * f / 0.15)
        R.actor(ground, x, y, sx=1 + 0.05 * e, sy=1 - 0.06 * e, flip=flip, **kw)
    elif f < 0.85:
        u = (f - 0.15) / 0.7
        st = 0.07 * (abs(math.cos(math.pi * u)) - 0.3)
        R.actor(air, x, y, h * math.sin(math.pi * u), sx=1 - st * 0.6, sy=1 + st, flip=flip, **kw)
    else:
        e = math.sin(math.pi * (f - 0.85) / 0.15)
        R.actor(ground, x, y, sx=1 + 0.06 * e, sy=1 - 0.07 * e, flip=flip, **kw)
        if dust:
            t0 = (b0 + (math.floor((b - b0) / period) + 0.85) * period) * R.spb
            R.puffs(x, y + 4, t0, b * R.spb, n=4, seed=int(b0 * 13 + x) % 211, life=0.5, rise=24, spread=110,
                    size=0.05, span=0.03, alpha=0.6)


def skid(R, pose, x, b, b0, y=GY, flip=False, **kw):
    # 急停：身体后仰回弹 + 一团尘土
    d = b - b0
    e = math.exp(-max(0.0, d) * 3) * math.cos(max(0.0, d) * 9)
    R.actor(pose, x, y, rot=-6 * e * (-1 if flip else 1), sx=1 + 0.05 * e, sy=1 - 0.05 * e, flip=flip,
            bend=-0.08 * e, **kw)
    R.puffs(x + (60 if not flip else -60), y + 2, b0 * R.spb, b * R.spb, n=6, seed=int(b0 * 7), life=0.8, rise=50,
            spread=160, size=0.07, drift=60 if not flip else -60, span=0.1, alpha=0.7)


def qmark(R, x, y, b, b0, b1, size=110, color=(98, 168, 222), ch="？", seed=0):
    # 头顶冒出的问号/感叹号：弹出、轻轻摇摆、淡出
    e = ease_back(seg(b, b0, b0 + 0.5), 2.2)
    a = 1 - seg(b, b1 - 0.4, b1)
    if e <= 0 or a <= 0:
        return
    t = R.frame / FPS
    R.paper_text(ch, x, y - 14 * e + 6 * math.sin(t * 3 + seed), size, color, rot=8 * math.sin(t * 2.4 + seed),
                 scale=e, alpha=a)


def hesitate(R, b, b0, x, y=GY, flip=False, **kw):
    # 犹豫：开头 0.9 拍抬一下蹄子想一想，之后蹄子放下，低头看水并慢慢左右歪头
    t = R.frame / FPS
    d = b - b0
    if 0 <= d < 0.9:
        R.actor("pony_think", x, y, flip=flip, rot=1.5 * math.sin(math.pi * d / 0.9), **kw)
    else:
        R.actor("pony_worry", x, y, flip=flip, rot=1.8 * math.sin(t * 1.1), bend=0.02 * math.sin(t * 1.5), **kw)


def pony_talk(R, b, x, spans, base, talk="pony_ask", flip=False, y=GY, seed=0.0, **kw):
    t = R.frame / FPS
    speaking = in_span(b, spans) and talking(t, seed)
    R.actor(talk if speaking else base, x, y, flip=flip, bend=0.02 * math.sin(t * 3.1) * (-1 if flip else 1), **kw)


def mom_actor(R, b, x, spans=(), nuzzle=None, flip=False, y=GY, **kw):
    t = R.frame / FPS
    if nuzzle and nuzzle[0] <= b < nuzzle[1]:
        # 蹭蹭头：低下头，前后轻轻蹭（身体跟着前后微移、轻微转动）
        e = ease_io(seg(b, nuzzle[0], nuzzle[0] + 0.6)) * (1 - ease_io(seg(b, nuzzle[1] - 0.6, nuzzle[1])))
        rub = math.sin(t * 5.5) * e
        sgn = -1 if flip else 1
        R.actor("mom_nuzzle", x + 7 * rub * sgn, y, flip=flip, rot=1.6 * rub * sgn, bend=0.025 * rub * sgn, **kw)
        return
    pose = "mom_talk" if in_span(b, spans) and talking(t, 2.0) else "mom_stand"
    R.actor(pose, x, y, flip=flip, bend=0.015 * math.sin(t * 1.7) * (-1 if flip else 1), **kw)


def ox_actor(R, b, x, spans=(), laugh=(), flip=False, y=GY, **kw):
    t = R.frame / FPS
    if in_span(b, laugh):
        R.actor("ox_laugh", x, y, flip=flip, rot=1.5 * math.sin(t * 9), sy=1 + 0.015 * math.sin(t * 18), **kw)
        return
    if in_span(b, spans):
        pose = "ox_talk" if talking(t, 4.0) else "ox_stand"
    else:
        pose = "ox_chew" if int(t * 1.6) % 2 else "ox_stand"   # 慢悠悠地嚼草
    R.actor(pose, x, y, flip=flip, bend=0.012 * math.sin(t * 1.3), **kw)


def squirrel_warn(R, x, y, t, flip=True, lift=0.0, **kw):
    # 松鼠着急地挥手跳脚
    pose = "squirrel_warn2" if int(t * 5) % 2 else "squirrel_warn"
    R.actor(pose, x, y, lift + 18 * abs(math.sin(t * 7.5)), flip=flip, rot=4 * math.sin(t * 15), **kw)


def drips(R, x, y, t, rate=6, seed=0, alpha=1.0):
    # 身上往下滴的水珠
    c = R.c
    for i in range(10):
        ph = (t * rate / 10 + rnd(i, seed)) % 1
        px = x + (rnd(i, seed + 1) - 0.5) * 110
        py = y + ph * ph * 120
        a = int(255 * alpha * (1 - ph))
        if a <= 0:
            continue
        r = 5 + 3 * rnd(i, seed + 2)
        c.drawCircle(px, py, r + 2.5, skia.Paint(AntiAlias=True, Color=skia.Color(255, 255, 255, a)))
        c.drawCircle(px, py, r, skia.Paint(AntiAlias=True, Color=skia.Color(120, 190, 240, a)))


def sparkles(R, x, y, b, b0, b1, n=6, spread=260, seed=0):
    # 开心时周围冒出的小星星
    for i in range(n):
        st = b0 + (b1 - b0) * rnd(i, seed)
        ph = seg(b, st, st + 1.4)
        if 0 < ph < 1:
            px = x + (rnd(i, seed + 1) - 0.5) * spread
            py = y - rnd(i, seed + 2) * spread * 0.6 - 40 * ph
            # 推迟到角色之后画，自动避开角色
            R.deferred.append(dict(name="star", x=px, y=py, scale=0.05 * math.sin(math.pi * ph), space=R.space,
                                   kw=dict(rot=ph * 120, anchor=(0.5, 0.5), shadow=0.1, boil=False)))


# ================= 片头 =================
def sc_title(R, b, t, S):
    # 开场钩子：第 0 帧就是奔跑的小马和弹出的标题；1.6 秒在河边急停，2.5 秒抛出问题“小马能过河吗？”
    # 随后老牛（浅）、松鼠（深）先后登场，把故事的矛盾提前亮出来
    C = CUES["title"]
    set_cam(R, kf(b, [(0, (760, 650, 1.3)), (C["stop"], (1060, 660, 1.32)), (5.6, (1060, 640, 1.26)), (6.7, (900, 560, 1.0)),
                      (16, (900, 560, 1.0))]))
    R.bg("bg_river", **BG_RIVER)
    clouds(R, t)
    river_fx(R, t, 0.3)
    R.butterfly("dragonfly", t, 1640, 640, ax=120, ay=40, ph=0.9, scale=0.28)
    px = 1170
    # 老牛从左边探进来
    ob = C["ox"]
    if b >= ob:
        ox = lerp(-420, OXX, ease_back(seg(b, ob, ob + 1.0), 1.3))
        ox_actor(R, b, ox, laugh=[(ob + 1.0, ob + 2.6)])
    # 松鼠从右边跳进来
    sb = C["squirrel"]
    sqx = 1450
    if b >= sb:
        if b < sb + 1.0:
            u = seg(b, sb, sb + 1.0)
            R.actor("squirrel_warn", lerp(2150, sqx, u), lerp(GY - 150, GY, u) - 260 * math.sin(math.pi * u), flip=True,
                    rot=-360 * ease_io(u), shadow=u > 0.7)
        elif b < sb + 3.0:
            squirrel_warn(R, sqx, GY, t)
        else:
            R.actor("squirrel_stand", sqx, GY, flip=True)
    # 小马：奔跑 -> 急停 -> 低头看水 -> 看看老牛、看看松鼠 -> 犯难
    st = C["stop"]
    if b < st:
        walker(R, GALLOP, b, -0.8, st, 260, px, rate=2.0, h=46, lean=0.07)
    elif b < st + 1.4:
        skid(R, "pony_scared", px, b, st)
        qmark(R, px + 190, GY - 330, b, st, st + 1.6, ch="！", color=(236, 122, 98))
    elif b < ob + 0.6:
        R.actor("pony_worry", px, GY, bend=0.02 * math.sin(t * 1.5))
        qmark(R, px + 200, GY - 340, b, C["voice"][1][1], ob + 0.6, size=120)
    elif b < ob + 1.2:
        turn(R, "pony_sack", px, b, ob + 0.6, ob + 1.2, False)
    elif b < sb + 0.4:
        R.actor("pony_ask", px, GY, flip=True)
    elif b < sb + 1.0:
        turn(R, "pony_sack", px, b, sb + 0.4, sb + 1.0, True)
    elif b < 12.0:
        R.actor("pony_scared" if b < sb + 1.8 else "pony_sack", px, GY)
    else:
        hesitate(R, b, 12.0, px)
        qmark(R, px + 30, GY - 430, b, 12.2, 15.6, size=110)
        qmark(R, px + 140, GY - 370, b, 12.8, 15.6, size=80, seed=2)
    R.fg_grass()
    R.screen()
    for i, ch in enumerate("小马过河"):
        e = ease_back(seg(b, -0.5 + i * 0.3, 0.4 + i * 0.3), 2.4)
        if e <= 0:
            continue
        R.paper_text(ch, 960 + (i - 1.5) * 205, 205 + 8 * math.sin(t * 2.2 + i * 1.3), 190, TITLE_COLORS[i], stroke=20,
                     rot=[-6, 4, -4, 6][i] * (0.5 + 0.5 * (1 - e)) + 2 * math.sin(t * 1.6 + i), scale=e)


# ================= 马棚：妈妈的嘱托 =================
def sc_home(R, b, t, S):
    C = CUES["home"]
    set_cam(R, kf(b, [(0, (780, 600, 1.25)), (8, (800, 590, 1.15)), (13, (780, 600, 1.3)), (22, (800, 600, 1.2)),
                      (26.5, (880, 570, 1.08)), (32, (1250, 540, 1.05))]))
    R.bg("bg_home", **BG_HOME)
    clouds(R, t)
    R.bird(t, 2.0, 230)
    R.butterfly("butterfly2", t, 1450, 560, ph=1.2)
    px = 1010
    load0, load1 = C["load"], C["load"] + 0.9
    # 马妈妈：说话、低头把口袋推给小马
    mom_actor(R, b, MOMX, spans=[(13.5, 21.7)], nuzzle=(load0 - 1.4, load0 + 0.5))
    # 口袋：先放在地上，再被拱起来落到小马背上
    sk = sprite_h(R, "sack", 120)
    sx0, sy0 = 830, GY + 6
    if b < load0:
        R.sprite("sack", sx0, sy0, sk, rot=-3)
    elif b < load1:
        u = seg(b, load0, load1)
        bx, by = act_pt(R, "pony_sack", px, GY, 0.36, 0.33, flip=True)
        R.sprite("sack", lerp(sx0, bx, u), lerp(sy0, by + 40, u) - 230 * math.sin(math.pi * u), sk * lerp(1, 0.85, u),
                 rot=-3 + 360 * ease_io(u))
    # 小马
    if b < C["nod"]:
        if b < 4.0:   # 小马从外面小跑回到妈妈身边
            walker(R, NTROT, b, 0.0, 4.0, 1650, px, rate=2.0, h=24)
        elif b < 8.0:   # 开心地蹦两下
            hopper(R, "pony_ncheer", "pony_stand", b, 4.0, 8.0, px, period=2.0, h=30, flip=True)
        elif b < 13.5:   # 歪头听妈妈说话
            R.actor("pony_stand", px, GY, flip=True, bend=-0.03 * math.sin(t * 1.4), rot=1.5 * math.sin(t * 1.1))
        else:
            R.actor("pony_stand", px, GY, flip=True, bend=0.02 * math.sin(t * 2.0))
    elif b < load0:
        nod = math.sin(math.pi * seg(b, C["nod"], C["nod"] + 1.2))
        R.actor("pony_nnod" if nod > 0.3 else "pony_stand", px, GY, flip=True, bend=0.04 * nod)
    elif b < load1:
        R.actor("pony_stand", px, GY, flip=True)
    elif b < C["turn"]:
        e = math.exp(-(b - load1) * 4) * math.sin((b - load1) * 14)
        # 口袋落背的“噗”一团纸云画在小马身后，只从轮廓边缘冒出来，不盖住小马
        R.puffs(px, GY - 220, load1 * R.spb, b * R.spb, n=6, seed=8, life=0.8, rise=70, spread=260, size=0.08, span=0.05)
        R.actor("pony_sack", px, GY, flip=True, sx=1 + 0.05 * e, sy=1 - 0.06 * e)
        sparkles(R, px, GY - 200, b, load1, load1 + 1.2, n=5, spread=200, seed=5)
    elif b < C["go"]:
        turn(R, "pony_sack", px, b, C["turn"], C["go"], True)
    else:
        walker(R, TROT, b, C["go"], 32.5, px, 2350, rate=2.2, h=30)
    R.fg_grass()


# ================= 路上 =================
def sc_road(R, b, t, S):
    set_cam(R, kf(b, [(0, (640, 560, 1.12)), (16, (1300, 560, 1.12))], ease=lambda x: x))
    R.bg("bg_road", u=0.5, v=0.5, scale=1.45)
    clouds(R, t)
    R.bird(t, 1.0, 200)
    if b < 8:
        px = walker(R, TROT, b, 0, 8, -250, 900, rate=2.2, h=30)
    elif b < 12:
        px = walker(R, GALLOP, b, 8, 12, 900, 1600, rate=2.0, h=48, lean=0.07)
    else:
        px = walker(R, TROT, b, 12, 16.5, 1600, 2400, rate=2.2, h=30)
    # 蝴蝶追着小马飞
    R.butterfly("butterfly1", t, px + 80, GY - 580, ax=140, ay=30, ph=0.5)
    R.butterfly("butterfly2", t, px - 160, GY - 440, ax=90, ay=30, ph=2.4)
    R.fg_grass()


# ================= 小河挡路 =================
def river_fx(R, t, seed=0):
    # 河面上的小涟漪（跟随背景视差）
    for i, (u, v) in enumerate([(0.83, 0.62), (0.9, 0.7), (0.79, 0.52), (0.95, 0.58)]):
        x, y = bg_point(R, u, v, BG_RIVER["u"], BG_RIVER["v"], BG_RIVER["scale"])
        R.ripple(x, y, (t + i * 0.7 + seed) % 2.2, life=2.0, r1=70, alpha=0.6, width=4)


def sc_river(R, b, t, S):
    C = CUES["river"]
    set_cam(R, kf(b, [(0, (960, 540, 1.0)), (4, (1060, 560, 1.06)), (7, (1180, 640, 1.4)), (14, (1220, 660, 1.5)),
                      (18, (1150, 620, 1.3)), (24, (1100, 600, 1.2))]))
    R.bg("bg_river", **BG_RIVER)
    clouds(R, t)
    river_fx(R, t)
    R.butterfly("dragonfly", t, 1550, 700, ax=200, ay=60, ph=0.4, scale=0.28)
    px = 1190
    if b < C["stop"]:
        walker(R, GALLOP, b, 0, C["stop"], -260, px, rate=2.0, h=46, lean=0.07)
    elif b < C["stop"] + 1.6:
        skid(R, "pony_scared", px, b, C["stop"])
        qmark(R, px + 60, GY - 380, b, C["stop"], C["stop"] + 2.2, ch="！", color=(236, 122, 98))
    elif b < C["look"]:
        R.actor("pony_sack", px, GY)
    elif b < C["think"]:
        R.actor("pony_worry", px, GY, bend=0.02 * math.sin(t * 1.5))
    else:
        hesitate(R, b, C["think"], px)
        qmark(R, px + 40, GY - 445, b, C["think"] + 0.5, 23.5)
        qmark(R, px + 190, GY - 400, b, C["think"] + 1.5, 23.5, size=80, seed=2)
    R.fg_grass()


# ================= 问老牛 =================
def sc_ox(R, b, t, S):
    C = CUES["ox"]
    set_cam(R, kf(b, [(0, (1100, 580, 1.12)), (5, (560, 620, 1.22)), (9, (760, 600, 1.12)), (15, (720, 610, 1.2)),
                      (17, (560, 640, 1.38)), (24.5, (600, 630, 1.32)), (27, (850, 580, 1.08)), (32, (1050, 560, 1.05))]))
    R.bg("bg_river", **BG_RIVER)
    clouds(R, t)
    river_fx(R, t, 0.5)
    R.butterfly("butterfly1", t, 520, 560, ax=200, ph=0.9)
    ox_actor(R, b, OXX, spans=[(17.4, 22.0)], laugh=[(22.0, 24.6)])
    px0, px1 = 1190, 880
    w0, w1 = C["walk"]
    r0, r1 = C["run"]
    if b < 5.0:
        R.actor("pony_worry", px0, GY, rot=1.8 * math.sin(t * 1.1))
    elif b < w0:
        turn(R, "pony_sack", px0, b, 5.0, w0, False)
    elif b < w1:
        walker(R, TROT, b, w0, w1, px0, px1, rate=2.0, h=26)
    elif b < C["nod"]:
        pony_talk(R, b, px1, [(10.2, 14.9)], "pony_sack", flip=True)
    elif b < C["nod"] + 1.0:
        R.actor("pony_nod", px1, GY, flip=True, bend=-0.04 * math.sin(math.pi * seg(b, C["nod"], C["nod"] + 1)))
    elif b < r0:
        turn(R, "pony_sack", px1, b, C["nod"] + 1.0, r0, True)
    else:
        walker(R, GALLOP, b, r0, r1, px1, 1150, rate=2.0, h=46, lean=0.07)
        if b < r0 + 2:
            sparkles(R, 1050, GY - 260, b, r0, r0 + 1.5, n=4, spread=220, seed=9)
    R.fg_grass()


# ================= 松鼠拦路 =================
def sc_squirrel(R, b, t, S):
    C = CUES["squirrel"]
    l0, l1 = C["leap"]
    sqx = 1450
    set_cam(R, kf(b, [(0, (900, 520, 1.0)), (1.4, (900, 520, 1.0)), (4, (1180, 620, 1.2)), (6, (1380, 720, 1.65)),
                      (13, (1420, 740, 1.75)), (17, (1460, 760, 1.95)), (22, (1440, 750, 1.85)), (23, (1300, 680, 1.4)),
                      (32, (1260, 660, 1.35))]))
    R.bg("bg_river", **BG_RIVER)
    clouds(R, t)
    river_fx(R, t, 1.0)
    ox_actor(R, b, OXX)
    # 松鼠：在树枝上喊 -> 跳下来拦住小马 -> 着急挥手 -> 难过地说起伙伴
    brx, bry = bg_point(R, 0.27, 0.215, BG_RIVER["u"], BG_RIVER["v"], BG_RIVER["scale"])
    if b < l0:
        if b < 0.5:
            R.actor("squirrel_stand", brx, bry, flip=False, shadow=False)
        else:
            squirrel_warn(R, brx, bry, t, flip=False, shadow=False)
    elif b < l1:
        u = seg(b, l0, l1)
        x = lerp(brx, sqx, ease_io(u))
        y = lerp(bry, GY, u) - 330 * math.sin(math.pi * u)
        R.actor("squirrel_warn", x, y, rot=360 * ease_io(seg(u, 0.15, 0.85)), flip=False, shadow=u > 0.7)
    elif b < l1 + 0.4:
        e = math.sin(math.pi * seg(b, l1, l1 + 0.4))
        R.actor("squirrel_stand", sqx, GY, flip=True, sx=1 + 0.12 * e, sy=1 - 0.15 * e)
        R.puffs(sqx, GY + 4, l1 * R.spb, b * R.spb, n=5, seed=4, life=0.6, rise=30, spread=120, size=0.05, span=0.05)
    elif b < 13.5:
        squirrel_warn(R, sqx, GY, t)
        qmark(R, sqx, GY - 260, b, 6.0, 12.5, size=90, ch="！", color=(236, 122, 98))
    elif b < C["sad"]:
        squirrel_warn(R, sqx, GY, t * 0.8)
    elif b < 22.4:
        R.actor("squirrel_sad", sqx, GY, flip=True, rot=1.5 * math.sin(t * 2), bend=0.02)
    else:
        R.actor("squirrel_stand", sqx, GY, flip=True)
    # 小马：慢慢走向河边 -> 被拦住 -> 吓一跳往后退
    px = 1170
    if b < l1:
        walker(R, TROT, b, 0, l1, 1150, px, rate=1.6, h=14, dust=False)
    elif b < l1 + 1.4:
        skid(R, "pony_scared", px, b, l1)
    elif b < C["scared"]:
        R.actor("pony_worry" if b > C["sad"] else "pony_sack", px, GY)
    elif b < C["scared"] + 1.2:
        u = seg(b, C["scared"], C["scared"] + 1.2)
        R.actor("pony_scared", lerp(px, 960, ease_out(u)), GY, 70 * math.sin(math.pi * u), rot=-8 * math.sin(math.pi * u))
    else:
        R.actor("pony_worry", 960, GY, sy=1 - 0.02 * math.sin(t * 9))
        R.sweat(*act_pt(R, "pony_worry", 960, GY, 0.8, 0.05), t)
    R.fg_grass()


# ================= 听谁的？ =================
def sc_confused(R, b, t, S):
    C = CUES["confused"]
    set_cam(R, kf(b, [(0, (900, 560, 1.02)), (3.5, (720, 580, 1.08)), (4.5, (1150, 580, 1.08)), (8, (1100, 600, 1.12)),
                      (11, (1020, 660, 1.4)), (14.5, (950, 600, 1.15)), (24, (820, 600, 1.12))]))
    R.bg("bg_river", **BG_RIVER)
    clouds(R, t)
    river_fx(R, t, 1.5)
    oxc = 230
    ox_actor(R, b, oxc, laugh=[(1.4, 3.6)])
    sqx = 1450
    if 4.2 <= b < 7.2:
        squirrel_warn(R, sqx, GY, t)
    else:
        R.actor("squirrel_stand", sqx, GY, flip=True)
    px = 900
    if b < C["look_ox"]:
        R.actor("pony_worry", 960, GY)
    elif b < C["look_ox"] + 0.6:
        turn(R, "pony_sack", lerp(960, px, seg(b, 0, 1.6)), b, C["look_ox"], C["look_ox"] + 0.6, False)
    elif b < 3.7:
        x = lerp(960, px, seg(b, 0, 1.6))
        R.actor("pony_sack", x, GY, flip=True)
        qmark(R, oxc + 120, GY - 570, b, 1.6, 3.8, size=90, ch="浅", color=(98, 168, 222))
    elif b < 4.3:
        turn(R, "pony_sack", px, b, 3.7, 4.3, True)
    elif b < 7.6:
        R.actor("pony_sack", px, GY)
        qmark(R, sqx, GY - 260, b, 4.4, 7.4, size=90, ch="深", color=(60, 110, 190))
    elif b < C["sad"] + 0.5:
        hesitate(R, b, C["think"] - 0.6, px)
        for k, (dx, dy, sz) in enumerate([(-40, -400, 120), (120, -360, 90), (-170, -330, 80)]):
            qmark(R, px + dx, GY + dy, b, C["think"] - 1 + k * 0.6, C["sad"] + 1, size=sz, seed=k)
    elif b < C["leave"][0]:
        R.actor("pony_sad", px, GY, sy=1 - 0.01 * math.sin(t * 2))
    elif b < C["leave"][0] + 0.6:
        turn(R, "pony_sad", px, b, C["leave"][0], C["leave"][0] + 0.6, False)
    else:
        walker(R, TROT, b, C["leave"][0] + 0.6, C["leave"][1] + 1, px, 700, rate=1.0, h=6, lean=-0.03, tilt=1.2)
    R.fg_grass()


# ================= 回家问妈妈 =================
def sc_home2(R, b, t, S):
    C = CUES["home2"]
    set_cam(R, kf(b, [(0, (1250, 560, 1.06)), (5, (800, 600, 1.2)), (15, (820, 610, 1.22)), (16.5, (980, 660, 1.45)),
                      (25, (960, 650, 1.42)), (26.5, (790, 630, 1.35)), (31, (790, 630, 1.35)), (32.5, (650, 610, 1.42)),
                      (48, (700, 600, 1.35)), (50, (850, 580, 1.12)), (56, (1200, 550, 1.05))]))
    R.bg("bg_home", **BG_HOME)
    clouds(R, t)
    R.butterfly("butterfly1", t, 1300, 520, ph=2.2)
    mom_actor(R, b, MOMX, spans=[(4.2, 7.5), (31.5, 39.4), (40.5, 47.4)], nuzzle=C["nuzzle"])
    px = 960
    a0, a1 = C["arrive"]
    if b < a1:
        walker(R, TROT, b, a0, a1, 2100, px, rate=1.4, h=10, lean=-0.03, tilt=1.5)
    elif b < 26.0:
        # 小马低着头说话：说话时身体轻轻起伏
        speak = in_span(b, [(11.0, 15.0), (16.0, 24.5)]) and talking(t)
        R.actor("pony_sad", px, GY, flip=True, sy=1 + (0.015 if speak else 0.0), bend=0.02 * math.sin(t * 2.3))
    elif b < 31.0:
        n0, n1 = C["nuzzle"]
        if b < n0:
            walker(R, TROT, b, 26.0, n0, px, NUZZLE_X, rate=2.0, h=8, dust=False)
        else:
            e = ease_io(seg(b, n0, n0 + 0.6)) * (1 - ease_io(seg(b, n1 - 0.6, n1)))
            rub = math.sin(t * 5.5) * e
            R.actor("pony_blink", NUZZLE_X - 4 * rub, GY, flip=True, blink=False, bend=0.035 * e + 0.012 * rub,
                    rot=1.2 * rub)
    elif b < 44.0:
        R.actor("pony_sack", NUZZLE_X, GY, flip=True, bend=0.015 * math.sin(t * 1.7))
    elif b < C["nod"]:
        R.actor("pony_sack", NUZZLE_X, GY, flip=True, rot=-2.0 * math.sin(t * 1.2))
        sparkles(R, NUZZLE_X - 40, GY - 300, b, 46.0, 48.5, n=3, spread=120, seed=12)
    elif b < C["cheer"]:
        nod = math.sin(math.pi * seg(b, C["nod"], C["nod"] + 1.4))
        R.actor("pony_nod" if nod > 0.25 else "pony_sack", NUZZLE_X, GY, flip=True, bend=-0.05 * nod)
    elif b < C["go"][0]:
        hopper(R, "pony_cheer", "pony_sack", b, C["cheer"], C["go"][0], NUZZLE_X, period=2.0, h=30, flip=True)
    elif b < C["go"][0] + 0.6:
        turn(R, "pony_sack", NUZZLE_X, b, C["go"][0], C["go"][0] + 0.6, True)
    else:
        walker(R, GALLOP, b, C["go"][0] + 0.6, C["go"][1] + 0.6, NUZZLE_X, 2350, rate=2.0, h=46, lean=0.07)
    R.fg_grass()


# ================= 回到河边 =================
def sc_back(R, b, t, S):
    C = CUES["back"]
    set_cam(R, kf(b, [(0, (900, 560, 1.02)), (4, (1150, 600, 1.15)), (6, (1320, 720, 1.6)), (11, (1330, 720, 1.6)),
                      (12, (1250, 700, 1.55)), (20.5, (1250, 690, 1.5)), (24, (1250, 620, 1.25))]))
    R.bg("bg_river", **BG_RIVER)
    clouds(R, t)
    river_fx(R, t, 2.0)
    R.butterfly("dragonfly", t, 1640, 560, ax=150, ay=40, ph=1.7, scale=0.28)
    sqx = 1450
    p0 = C["pop"]
    if b < p0:
        pass
    elif b < p0 + 0.8:
        u = seg(b, p0, p0 + 0.8)
        R.actor("squirrel_warn", sqx, GY + 60 * (1 - u), 120 * math.sin(math.pi * u), flip=True, alpha=min(1, u * 4))
    elif b < 11.0:
        squirrel_warn(R, sqx, GY, t)
        qmark(R, sqx, GY - 260, b, 6.5, 10.8, size=90, ch="！", color=(236, 122, 98))
    elif b < C["aside"]:
        R.actor("squirrel_stand", sqx, GY, flip=True, rot=1.5 * math.sin(t * 2))
    elif b < C["aside"] + 1.2:
        # 让路：从小马头顶跳过去，落到后面
        u = seg(b, C["aside"], C["aside"] + 1.2)
        R.actor("squirrel_warn", lerp(sqx, 820, ease_io(u)), GY - 420 * math.sin(math.pi * u), flip=True,
                rot=-360 * ease_io(seg(u, 0.2, 0.8)), shadow=u > 0.8)
    else:
        R.actor("squirrel_wave", 820, GY, flip=False, rot=4 * math.sin(t * 8))
    px = 1120
    a0, a1 = C["arrive"]
    e0, e1 = C["edge"]
    if b < a1:
        walker(R, GALLOP, b, a0, a1, -260, px, rate=2.0, h=46, lean=0.07)
    elif b < a1 + 1.4:
        skid(R, "pony_sack", px, b, a1)
    elif b < 12.0:
        R.actor("pony_scared" if 6.5 <= b < 8.0 else "pony_sack", px, GY)
    elif b < e0:
        pony_talk(R, b, px, [(13.4, 20.8)], "pony_sack")
        if b > 18.0:
            sparkles(R, px + 60, GY - 300, b, 18.0, 20.5, n=3, spread=160, seed=21)
    elif b < e1:
        walker(R, TROT, b, e0, e1, px, 1400, rate=1.2, h=10, dust=False)
    else:
        u = seg(b, e1, 24)
        R.actor("pony_step", 1400, GY, bend=-0.02 * u)
    R.fg_grass()


# ================= 小心过河 =================
WL = 840   # 河水前景条的水面线


def depth(x):
    # 河水深度随位置变化：岸边浅，中间到膝盖
    return 24 + 56 * ease_io(seg(x, 220, 760)) * (1 - ease_io(seg(x, 1350, 1850)))


def compare_card(R, x, y, pose, water, label, a, scale_mul):
    # 对比小卡片：同样的河水，在老牛身上很浅，在松鼠身上很深
    if a <= 0.01:
        return
    c = R.c
    rw, rh = 300, 300
    rect = skia.Rect.MakeXYWH(x - rw / 2, y - rh / 2, rw, rh)
    c.save()
    c.translate(x, y)
    c.scale(0.6 + 0.4 * ease_back(a, 1.6), 0.6 + 0.4 * ease_back(a, 1.6))
    c.translate(-x, -y)
    c.drawRoundRect(rect.makeOffset(6, 8), 34, 34, skia.Paint(AntiAlias=True, Color=skia.Color(60, 35, 15, int(70 * a))))
    c.drawRoundRect(rect, 34, 34, skia.Paint(AntiAlias=True, Color=skia.Color(253, 247, 233, int(250 * a))))
    c.save()
    c.clipRRect(skia.RRect.MakeRectXY(rect.makeInset(12, 12), 26, 26), True)
    c.drawRect(rect, skia.Paint(Color=skia.Color(214, 236, 246, int(255 * a))))
    foot = y + rh / 2 - 40
    R.record = False
    R.actor(pose, x, foot, scale_mul=scale_mul, alpha=a, shadow=False, blink=False)
    R.record = True
    top = foot - water
    p = skia.Path()
    p.moveTo(x - rw, top)
    for i in range(25):
        xx = x - rw / 2 + i * rw / 24
        p.lineTo(xx, top + 6 * math.sin(i * 1.3 + R.frame * 0.25))
    p.lineTo(x + rw, y + rh)
    p.lineTo(x - rw, y + rh)
    p.close()
    c.drawPath(p, skia.Paint(AntiAlias=True, Color=skia.Color(90, 160, 220, int(190 * a))))
    c.drawPath(p, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=5, Color=skia.Color(255, 255, 255, int(220 * a))))
    c.restore()
    c.drawRoundRect(rect.makeInset(7, 7), 28, 28, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=3,
                                                             Color=skia.Color(225, 196, 160, int(255 * a)),
                                                             PathEffect=skia.DashPathEffect.Make([10, 7], 0)))
    c.restore()
    R.paper_text(label, x, y - rh / 2 - 6, 64, (98, 168, 222) if label == "浅" else (60, 110, 190), scale=a, alpha=a)


def sc_cross(R, b, t, S):
    C = CUES["cross"]
    w0, w1 = C["wade"]
    o0, o1 = C["out"]
    x0, x1, x2 = 200, 960, 2350
    if b < w0:
        px = x0
    elif b < w1:
        px = lerp(x0, x1, seg(b, w0, w1))
    elif b < o0:
        px = x1
    else:
        px = lerp(x1, x2, seg(b, o0, o1))
    set_cam(R, kf(b, [(0, (520, 640, 1.4)), (4, (560, 640, 1.35)), (8.5, (700, 650, 1.3)), (10.5, (760, 730, 1.85)),
                      (14, (820, 720, 1.8)), (15.5, (900, 640, 1.35)), (19.5, (960, 640, 1.3)), (21, (960, 560, 1.02)),
                      (27.5, (960, 560, 1.02)), (32, (1500, 580, 1.15))]))
    R.bg("bg_ford", u=0.5, v=0.5)
    clouds(R, t)
    R.butterfly("dragonfly", t, px + 80, 560, ax=220, ay=70, ph=0.6, scale=0.3)
    spb = R.spb
    yfun = lambda x: WL + depth(x)

    # 跳出水面的小鱼（画在水条后面，入水出水处自然被遮住）
    f0, f1 = C["fish"]
    for k, (fx0, fx1, fb0, nm) in enumerate([(px + 230, px + 520, f0 + 0.3, "fish1"), (px + 600, px + 300, f0 + 2.2, "fish2"),
                                              (px - 560, px - 270, f0 + 3.6, "fish1")]):
        u = seg(b, fb0, fb0 + 1.3)
        if 0 < u < 1:
            fx = lerp(fx0, fx1, u)
            fy = WL + 40 - 260 * math.sin(math.pi * u)
            # 鱼图朝左：往右游时镜像，沿弧线切线方向转动
            ang = math.degrees(math.atan2(-260 * math.pi * math.cos(math.pi * u) / 1.3, (fx1 - fx0) / 1.3))
            right = fx1 > fx0
            R.sprite(nm, fx, fy, sprite_h(R, nm, 70), rot=ang if right else ang - 180, flip=right, anchor=(0.5, 0.5),
                     shadow=0.15, boil=False)
        for tb, xx in ((fb0, fx0), (fb0 + 1.3, fx1)):
            if 0 <= b - tb < 1.2:
                R.drops(xx, WL + 10, tb * spb, b * spb, n=10, seed=k * 5 + int(tb), speed=420, life=0.8)
                R.ripple(xx, WL + 14, (b - tb) * spb, life=1.3, r1=110)

    # 小马
    def step_fx(xj, tb, j):
        R.ripple(xj + 30, WL + 16, (b - tb) * spb, life=1.5, r1=140)
        if 0 <= b - tb < 0.8:
            R.drops(xj + 40, WL + 6, tb * spb, b * spb, n=6, seed=j * 3, speed=300, life=0.6)

    if b < C["step"]:
        R.actor("pony_sack", x0, yfun(x0))
    elif b < w0:
        u = seg(b, C["step"], C["down"])
        R.actor("pony_step", x0, yfun(x0), rot=-3 * ease_io(u), bend=-0.03 * u)
        if b >= C["down"]:
            R.ripple(x0 + 120, WL + 14, (b - C["down"]) * spb, life=1.6, r1=150)
            R.drops(x0 + 120, WL + 6, C["down"] * spb, b * spb, n=8, seed=2, speed=320, life=0.7)
    elif b < w1:
        walker(R, TROT, b, w0, w1, x0, x1, rate=0.9, h=8, tilt=1.5, lean=0.0, yfun=yfun, step_cb=step_fx)
    elif b < o0:
        # 站在河中央：低头看看没到膝盖的河水，再开心地点点头
        if b < 23.5:
            R.actor("pony_worry", x1, yfun(x1), bend=0.015 * math.sin(t * 1.6))
        elif b < 25.5:
            nod = math.sin(math.pi * seg(b, 23.5, 25.5))
            R.actor("pony_nod" if nod > 0.3 else "pony_sack", x1, yfun(x1), bend=0.04 * nod)
        else:
            R.actor("pony_sack", x1, yfun(x1), rot=1.5 * math.sin(t * 2))
            sparkles(R, x1, WL - 300, b, 25.5, 27.5, n=4, spread=220, seed=41)
    else:
        walker(R, TROT, b, o0, o1, x1, x2, rate=2.0, h=18, yfun=yfun, step_cb=step_fx)
    # 腿边一圈圈涟漪
    if w0 <= b < o1:
        for i in range(2):
            R.ripple(px + 10, WL + 18, (t + i * 0.9) % 1.8, life=1.8, r1=170, alpha=0.6, width=4)
    # 前景河水与睡莲：前后两层河水条，后层盖住腿，前层更快地流动，形成纸片层叠的水面
    R.flush_fx()
    R.water(WL - 24, h=230, flow=26)
    for i, (lx, ls) in enumerate([(380, 0.9), (1640, 1.0), (2400, 0.8)]):
        R.sprite("lilypad", lx, WL + 60 + 6 * math.sin(t * 1.4 + i), sprite_h(R, "lilypad", 90) * ls,
                 rot=4 * math.sin(t * 0.9 + i), anchor=(0.5, 0.6), shadow=0.12, boil=False)
    R.water(WL + 150, h=280, flow=48, amp=0.8)
    # “原来……”：两张对比卡片
    R.screen()
    compare_card(R, 300, 260, "ox_stand", 55, "浅", ease_io(seg(b, 20.6, 21.6)) * (1 - ease_io(seg(b, 29.6, 30.6))), 0.44)
    compare_card(R, 1620, 260, "squirrel_sad", 230, "深", ease_io(seg(b, 26.5, 27.4)) * (1 - ease_io(seg(b, 29.6, 30.6))), 1.1)


# ================= 磨坊 =================
MLX, MLY = 1220, 890


def mill_pt(R, u, v, ms):
    w, h = R.A.size("mill")
    return MLX + (u - 0.5) * w * ms, MLY - (1 - v) * h * ms


def sc_mill(R, b, t, S):
    C = CUES["mill"]
    set_cam(R, kf(b, [(0, (700, 570, 1.12)), (5, (960, 590, 1.12)), (8.5, (1000, 590, 1.1)), (10, (1400, 560, 1.45)),
                      (12.5, (1350, 560, 1.4)), (14, (1050, 570, 1.08)), (20, (1000, 560, 1.05))]))
    R.bg("bg_millside", u=0.45, v=0.5)
    clouds(R, t)
    R.bird(t, 6.0, 190, flip=True)
    ms = 560 / R.A.size("mill")[1]
    hx, hy = mill_pt(R, 0.935, 0.62, ms)
    ws = 330 / R.A.size("waterwheel")[1]
    # 水车（画在磨坊后面）+ 从叶片上洒下的水珠
    R.sprite("waterwheel", hx + 40, hy, ws, rot=-t * 32, anchor=(0.5, 0.5), shadow=0.25, boil=False)
    for k in range(3):
        tb = (math.floor(t / 0.7) - k) * 0.7
        R.drops(hx + 60, hy + 150, tb, t, n=6, seed=int(tb * 10) % 97, speed=260, life=0.7)
    R.sprite("mill", MLX, MLY, ms, shadow=0.3)
    dx, dy = mill_pt(R, 0.6, 0.86, ms)
    px = 790
    u0 = C["unload"]
    sk = sprite_h(R, "sack", 110)
    # 口袋从背上卸到磨坊门口
    if b >= u0:
        u = seg(b, u0, u0 + 0.9)
        bx, by = act_pt(R, "pony_sack", px, GY, 0.36, 0.33)
        R.sprite("sack", lerp(bx, dx - 70, u), lerp(by + 40, dy + 10, u) - 200 * math.sin(math.pi * u), sk,
                 rot=lerp(0, 368, ease_io(u)))
        if b >= u0 + 0.9:
            R.puffs(dx - 70, dy + 12, (u0 + 0.9) * R.spb, b * R.spb, n=4, seed=6, life=0.6, rise=30, spread=100, size=0.05, span=0.04)
    a0, a1 = C["arrive"]
    c0, c1 = C["cheer"]
    if b < a1:
        walker(R, TROT, b, a0, a1, -260, px, rate=2.2, h=28)
        x = lerp(-260, px, seg(b, a0, a1))
        drips(R, x, GY - 70, t, alpha=1 - seg(b, 3, 5))
    elif b < u0:
        R.actor("pony_sack", px, GY)
    elif b < u0 + 0.9:
        R.actor("pony_stand", px, GY, sy=1 + 0.03 * math.sin(math.pi * seg(b, u0, u0 + 0.9)))
    elif b < c0:
        nod = math.sin(math.pi * seg(b, u0 + 1.4, u0 + 2.6))
        R.actor("pony_nnod" if nod > 0.3 else "pony_stand", px, GY)
    else:
        hopper(R, "pony_ncheer", "pony_stand", b, c0, c1, px, period=2.0, h=40, flip=int((b - c0) / 4) % 2 == 1)
        sparkles(R, px, GY - 260, b, c0, c1, n=10, spread=320, seed=17)
    R.fg_grass()


# ================= 结尾（圆舞曲） =================
WARM = [1.04, 0, 0, 0, 0.03, 0, 0.96, 0, 0, 0.0, 0, 0, 0.86, 0, 0, 0, 0, 0, 1, 0]


def sc_finale(R, b, t, S):
    C = CUES["finale"]
    set_cam(R, kf(b, [(0, (1300, 580, 1.15)), (12, (880, 600, 1.2)), (24, (840, 620, 1.3)), (40, (900, 580, 1.15)),
                      (52, (960, 540, 1.0)), (60, (960, 540, 1.0))]))
    R.bg("bg_sunset", u=0.5, v=0.5)
    R.tint = WARM
    R.fireflies(t, n=14, seed=3, alpha=ease_io(seg(b, 24, 40)), area=(0, 300, 1920, 820))
    # 远处：松鼠坐在老牛背上挥手
    ox_actor(R, b, 210, laugh=[(30, 46)], scale_mul=0.55, y=GY - 30)
    sx, sy = act_pt(R, "ox_stand", 210, GY - 30, 0.42, 0.18, scale_mul=0.55)
    R.actor("squirrel_wave" if int(t * 2.5) % 2 or b < 30 else "squirrel_stand", sx, sy + 20, flip=False, scale_mul=0.9,
            shadow=False, rot=4 * math.sin(t * 6))
    # 马妈妈在家门口等
    mx = 640
    meet = C["meet"]
    mom_actor(R, b, mx, nuzzle=(meet + 1.0, meet + 8.0))
    px = mx + NUZZLE_X - MOMX
    if b < meet:
        walker(R, NTROT, b, 0, meet, 2250, px, rate=1.6, h=30)
    elif b < meet + 9:
        e = ease_io(seg(b, meet + 1.0, meet + 1.6)) * (1 - ease_io(seg(b, meet + 7.4, meet + 8.0)))
        rub = math.sin(t * 5.5) * e
        R.actor("pony_nblink" if e > 0.2 else "pony_stand", px - 4 * rub, GY, flip=True, blink=False,
                bend=0.035 * e + 0.012 * rub, rot=1.2 * rub)
        sparkles(R, (px + mx) / 2 + 60, GY - 470, b, meet + 1.5, meet + 6, n=4, spread=160, seed=31)
    else:
        hopper(R, "pony_ncheer", "pony_stand", b, meet + 9, 52, px, period=3.0, h=36, flip=True)
    R.fg_grass()
    R.screen()
    e = ease_back(seg(b, 50, 51.2), 2.2)
    if e > 0:
        R.paper_text("完", 960, 330 + 8 * math.sin(t * 2), 230, (246, 196, 82), rot=-4, scale=e)


FUN = dict(title=sc_title, home=sc_home, road=sc_road, river=sc_river, ox=sc_ox, squirrel=sc_squirrel,
           confused=sc_confused, home2=sc_home2, back=sc_back, cross=sc_cross, mill=sc_mill, finale=sc_finale)
CUT_IN = set()   # 直接硬切进入的幕
HALF = 0.32      # 撕纸转场半时长（秒）


def draw_scene(R, name, t):
    S = TL[name]
    lt = max(0.0, t - S["start"])
    b = lt * S["bpm"] / 60
    R.cam, R.shake, R.tint, R.wind = (960.0, 540.0, 1.0), (0.0, 0.0), None, 0.0
    R.spb = 60 / S["bpm"]
    R.scene_name = name
    FUN[name](R, b, lt, S)


def scene_at(t):
    idx = 0
    for i, n in enumerate(ORDER):
        if TL[n]["start"] <= t:
            idx = i
    return idx


def draw_caption(R, t):
    # 旁白字幕：与配音同步，淡入淡出
    R.tint = None
    for sp in SPANS:
        if sp["t0"] - 0.2 <= t <= sp["t1"] + 0.5:
            R.caption(sp["text"], t, sp["t0"] - 0.2, sp["t1"] + 0.5, 60.0)


def render_frame(R, f):
    t = f / FPS
    R.begin(f)
    i = scene_at(t)
    name = ORDER[i]
    start = TL[name]["start"]
    old = None
    if i > 0 and t - start < HALF and name not in CUT_IN:
        old, p = ORDER[i - 1], (t - (start - HALF)) / (2 * HALF)
    elif i + 1 < len(ORDER) and TL[ORDER[i + 1]]["start"] - t < HALF and ORDER[i + 1] not in CUT_IN:
        nxt = ORDER[i + 1]
        old, name, p = name, nxt, (t - (TL[nxt]["start"] - HALF)) / (2 * HALF)
    draw_scene(R, name, t)
    if old:
        # 撕纸转场：旧画面从右往左被撕走
        xe = lerp(W + 80, -80, ease_io(p))
        c = R.c
        c.restoreToCount(1)
        c.save()
        clip = R.torn_clip(xe, seed=i)
        c.clipPath(clip, skia.ClipOp.kIntersect, True)
        R.base = c.getSaveCount()
        draw_scene(R, old, t)
        R.base = 1
        c.restoreToCount(1)
        c.save()
        edge = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=14,
                          Color=skia.Color(255, 251, 240), StrokeJoin=skia.Paint.kRound_Join)
        sh = skia.Paint(edge)
        sh.setColor(skia.Color(40, 25, 10, 90))
        sh.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 8))
        c.translate(6, 0)
        c.drawPath(clip, sh)
        c.translate(-6, 0)
        c.drawPath(clip, edge)
    draw_caption(R, t)
    fb = seg(t, TOTAL - 2.6, TOTAL - 0.8)   # 开头直接亮画面（前 3 秒是关键），只在片尾淡出
    R.post(fb)
    return R.arr


# ---------------- 入口 ----------------
_R = None
LOG = "[render 视频渲染]"


def _init():
    global _R
    _R = Renderer(Assets())


def _render_range(args):
    k, f0, f1, path = args
    if _R is None:
        _init()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(f0, f1):
        p.stdin.write(render_frame(_R, f).tobytes())
    p.stdin.close()
    p.wait()
    return k


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1]
    if mode == "still":
        _init()
        for s in sys.argv[2:]:
            t = float(s)
            arr = render_frame(_R, int(round(t * FPS)))
            skia.Image.fromarray(arr.copy(), colorType=skia.kBGRA_8888_ColorType).save(str(OUT / f"still_{t:07.2f}.png"), skia.kPNG)
        return
    # 视频：按帧区间并行渲染，再拼接并合入配乐
    t0 = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    t1 = float(sys.argv[3]) if len(sys.argv) > 3 else TOTAL
    workers = 6   # 机器内存有限：减少并行进程，只是更慢，画质不变
    f0, f1 = int(t0 * FPS), int(t1 * FPS)
    segdir = OUT / "seg"
    segdir.mkdir(parents=True, exist_ok=True)
    for old in segdir.glob("seg_*.mp4"):
        old.unlink()
    n = max(1, min(workers * 3, (f1 - f0) // 24))
    bounds = [f0 + (f1 - f0) * i // n for i in range(n + 1)]
    jobs = [(i, bounds[i], bounds[i + 1], segdir / f"seg_{i:03d}.mp4") for i in range(n)]
    st = time.time()
    with ProcessPoolExecutor(workers, initializer=_init) as ex:
        for k in ex.map(_render_range, jobs):
            print(f"{LOG} 片段 {k + 1}/{n} 完成，用时 {time.time() - st:.0f}s", flush=True)
    lst = segdir / "list.txt"
    lst.write_text("".join(f"file '{j[3].as_posix()}'\n" for j in jobs), encoding="utf-8")
    out = OUT / ("little_horse.mp4" if (t0 == 0 and t1 == TOTAL) else f"preview_{t0:.0f}_{t1:.0f}.mp4")
    music = OUT / "music.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ss", f"{t0}",
                    "-i", str(music), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", "-movflags", "+faststart", str(out)], check=True)
    print(f"{LOG} 输出 {out}")


if __name__ == "__main__":
    main()
