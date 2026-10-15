# 自检工具：逐帧只算位置不画图，检查角色、飞虫、道具、文字之间的纸片重叠，按时间段汇总
import sys
import time
from collections import defaultdict

from engine import FPS, Assets, Renderer
from scenes import render_frame
from timeline import TOTAL

LOG = "[audit 重叠检查]"

# 不参与检查的装饰元素：尘土云、星星、叶子、睡莲、天上的云
IGNORE = {"cloud1", "cloud2", "cloud3", "leaf1", "leaf2", "leaf3", "leaf4", "lilypad"}
# 允许接触的组合：(幕名或 *, 元素A, 元素B)
ALLOW = {("finale", "ox", "squirrel"), ("*", "mill", "waterwheel"), ("*", "mill", "sack"), ("*", "fish1", "fish2"),
         ("home", "pony", "sack"), ("mill", "pony", "sack"), ("finale", "mom", "pony"), ("home2", "mom", "pony")}


def who(kind, name):
    if kind == "caption":
        return "caption"
    if kind == "actor":
        return name.split("_")[0]
    if kind == "text":
        return "text:" + name
    return name


def allowed(scene, a, b):
    if a.startswith("text:") and b.startswith("text:"):
        return True
    for sc, x, y in ALLOW:
        if sc in ("*", scene) and {a, b} == {x, y}:
            return True
    return False


def inset(b, k=0.1):
    x0, y0, x1, y1 = b
    dx, dy = (x1 - x0) * k, (y1 - y0) * k
    return x0 + dx, y0 + dy, x1 - dx, y1 - dy


def overlap(a, b, ka=0.1, kb=0.1):
    # 角色/道具的包围盒内缩（纸片四角透明）；文字外扩（文字之间、文字与角色要留出空隙）
    ax0, ay0, ax1, ay1 = inset(a, ka)
    bx0, by0, bx1, by1 = inset(b, kb)
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    amin = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return iw * ih / max(1.0, amin)


def on_screen(b):
    x0, y0, x1, y1 = b
    return x1 > 0 and x0 < 1920 and y1 > 0 and y0 < 1080


def main():
    t0 = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    t1 = float(sys.argv[2]) if len(sys.argv) > 2 else TOTAL
    R = Renderer(Assets())
    R.dry = True
    hits = defaultdict(list)
    st = time.time()
    for f in range(int(t0 * FPS), int(t1 * FPS)):
        render_frame(R, f)
        items = [(who(k, n), (x0, y0, x1, y1), sc) for k, n, x0, y0, x1, y1, sc in R.boxes
                 if n not in IGNORE and on_screen((x0, y0, x1, y1))]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (a, ba, sa), (b, bb, sb) = items[i], items[j]
                cross = "caption" in (a, b)
                if (sa != sb and not cross) or a == b or allowed(sa, a, b):
                    continue
                ka = -0.08 if a.startswith("text:") else 0.1
                kb = -0.08 if b.startswith("text:") else 0.1
                r = overlap(ba, bb, ka, kb)
                limit = 0.0 if (a.startswith("text:") or b.startswith("text:")) else 0.04
                if r > limit:
                    key = (sa,) + tuple(sorted((a, b)))
                    hits[key].append((f / FPS, r))
    # 合并成连续时间段
    for key, lst in sorted(hits.items(), key=lambda kv: kv[1][0][0]):
        spans, cur = [], None
        for t, r in lst:
            if cur and t - cur[1] <= 1.5 / FPS:
                cur[1], cur[2] = t, max(cur[2], r)
            else:
                cur = [t, t, r]
                spans.append(cur)
        for a, b, r in spans:
            print(f"{LOG}[{key[0]}] {key[1]} × {key[2]}  {a:7.2f}-{b:7.2f}s  最大重叠 {r:.0%}", flush=True)
    print(f"{LOG} 完成，用时 {time.time() - st:.0f}s，问题组合 {len(hits)}")


if __name__ == "__main__":
    main()
