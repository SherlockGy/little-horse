# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "numpy", "scipy"]
# ///
# 素材预处理：纯色背景抠图、裁边、拆分道具拼版、背景整理
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "raw"
CUT = ROOT / "assets" / "cut"
LOG = "[prep 素材预处理]"

MAGENTA = {"fg_grass", "fg_water", "props"}
PROP_NAMES = ["sack", "floursack", "fish1", "fish2", "dragonfly", "lilypad", "acorn", "wheat"]


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def key_alpha(rgb, key):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    # 背景色“纯度”：绿底看 g 超出多少，品红底看 r、b 超出 g 多少
    k = g - np.maximum(r, b) if key == "green" else np.minimum(r, b) - g

    # 只把与图像边缘连通的强背景区域视为背景，避免误伤角色内部的绿色叶子
    strong = k > 70
    lab, _ = ndimage.label(strong)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    bg = np.isin(lab, border[border > 0])
    # 封闭的空隙（如手臂与身体之间）若是极纯的背景色也去掉
    bg |= k > 190
    band = ndimage.binary_dilation(bg, iterations=4)

    alpha = np.ones(k.shape, np.float32)
    soft = 1 - smoothstep(25, 150, k)
    alpha[band] = soft[band]
    alpha[bg] = np.minimum(alpha[bg], 0.0)

    # 去溢色：边缘半透明处把背景色成分压回去
    out = rgb.copy()
    edge = band & (alpha > 0)
    if key == "green":
        out[..., 1] = np.where(edge, np.minimum(g, np.maximum(r, b) + 8), g)
    else:
        lim = g + 8
        out[..., 0] = np.where(edge, np.minimum(r, lim), r)
        out[..., 2] = np.where(edge, np.minimum(b, lim), b)
    return out, alpha


def to_rgba(rgb, alpha):
    a = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    return np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), a])


def crop(rgba, pad=6, info=None):
    ys, xs = np.nonzero(rgba[..., 3] > 8)
    y0, y1 = max(0, ys.min() - pad), min(rgba.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(rgba.shape[1], xs.max() + pad + 1)
    if info is not None:
        # 记录裁切偏移与原图尺寸，供动作帧按原画布对齐
        info.update(crop=[int(x0), int(y0)], raw=[int(rgba.shape[1]), int(rgba.shape[0])])
    return rgba[y0:y1, x0:x1]


def main():
    CUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    for f in sorted(RAW.glob("*.png")):
        name = f.stem
        im = np.asarray(Image.open(f).convert("RGB")).astype(np.float32)
        if name.startswith("bg_") or name == "paper_texture":
            Image.fromarray(im.astype(np.uint8)).save(CUT / f"{name}.png")
            meta[name] = dict(w=im.shape[1], h=im.shape[0])
            continue
        key = "magenta" if name in MAGENTA else "green"
        rgb, alpha = key_alpha(im, key)
        rgba = to_rgba(rgb, alpha)

        if name == "props":
            # 按连通域拆分道具，按行优先排序后命名
            lab, cnt = ndimage.label(rgba[..., 3] > 40)
            objs = ndimage.find_objects(lab)
            items = []
            for i, sl in enumerate(objs):
                area = (lab[sl] == i + 1).sum()
                if area < 2000:
                    continue
                cy, cx = (sl[0].start + sl[0].stop) / 2, (sl[1].start + sl[1].stop) / 2
                items.append((cy, cx, i + 1, sl))
            items.sort(key=lambda t: (round(t[0] / 200), t[1]))
            print(f"{LOG} 道具拼版拆出 {len(items)} 个")
            for nm, (cy, cx, idx, sl) in zip(PROP_NAMES, items):
                sub = rgba[sl].copy()
                sub[..., 3] = np.where(lab[sl] == idx, sub[..., 3], 0)
                sub = crop(sub)
                Image.fromarray(sub).save(CUT / f"{nm}.png")
                meta[nm] = dict(w=sub.shape[1], h=sub.shape[0])
            continue

        info = {}
        rgba = crop(rgba, info=info)
        Image.fromarray(rgba).save(CUT / f"{name}.png")
        meta[name] = dict(w=rgba.shape[1], h=rgba.shape[0], **info)
        print(f"{LOG}[{name}] {rgba.shape[1]}x{rgba.shape[0]}")
    (CUT / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
