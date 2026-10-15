# 全片时间轴：画面、配乐、旁白共用。按“速度 + 小节数”定义每一幕，动作与旁白都用幕内拍号定位

# (幕名, 每分钟拍数, 每小节拍数, 小节数)
SCENES = [
    ("title", 96, 4, 4),       # 片头：纸片标题弹出，小马奔跑
    ("home", 100, 4, 8),       # 马棚：妈妈让小马驮麦子去磨坊
    ("road", 120, 4, 4),       # 路上小跑
    ("river", 100, 4, 6),      # 小河挡路，犹豫
    ("ox", 100, 4, 8),         # 问老牛：水很浅
    ("squirrel", 100, 4, 8),   # 松鼠拦路：水很深
    ("confused", 90, 4, 6),    # 听谁的？没了主意，回家
    ("home2", 90, 4, 14),      # 妈妈：自己去试一试
    ("back", 100, 4, 6),       # 回到河边，谢谢松鼠
    ("cross", 80, 4, 8),       # 小心过河
    ("mill", 100, 4, 5),       # 送到磨坊
    ("finale", 150, 3, 20),    # 圆舞曲结尾
]

# 每幕的旁白入点 voice=[(句序号, 起始拍)] 与关键动作拍点
CUES = {
    "title": dict(voice=[(0, 0.4), (1, 4.0)], stop=2.6, ox=6.0, squirrel=9.4),
    "home": dict(voice=[(0, 1.0), (1, 8.5), (2, 13.5), (3, 23.0)], nod=23.0, load=24.4, turn=26.6, go=27.4),
    "road": dict(voice=[(0, 2.0)]),
    "river": dict(voice=[(0, 0.5), (1, 7.0), (2, 14.5)], stop=4.0, look=7.5, think=14.5),
    "ox": dict(voice=[(0, 0.5), (1, 8.0), (2, 16.0), (3, 25.5)], walk=(6.0, 9.5), nod=25.5, run=(27.2, 31.0)),
    "squirrel": dict(voice=[(0, 0.5), (1, 6.0), (2, 13.5), (3, 23.0)], leap=(1.4, 4.0), sad=17.0, scared=23.0),
    "confused": dict(voice=[(0, 1.0), (1, 8.5), (2, 14.5)], look_ox=1.0, look_sq=4.0, think=8.5, sad=12.0, leave=(15.0, 23.0)),
    "home2": dict(voice=[(0, 2.0), (1, 9.0), (2, 16.0), (3, 26.0), (4, 31.5), (5, 40.5), (6, 49.0)],
                  arrive=(0.0, 5.0), nuzzle=(27.0, 31.0), nod=49.0, cheer=51.0, go=(53.0, 56.0)),
    "back": dict(voice=[(0, 1.0), (1, 6.5), (2, 12.0)], arrive=(0.0, 4.0), pop=4.5, aside=19.0, edge=(21.0, 23.5)),
    "cross": dict(voice=[(0, 1.0), (1, 9.5), (2, 15.0), (3, 20.5), (4, 26.5)],
                  step=1.5, down=3.0, wade=(4.0, 20.0), fish=(15.0, 19.5), out=(27.5, 32.0)),
    "mill": dict(voice=[(0, 1.0), (1, 9.0)], arrive=(0.0, 5.0), unload=6.0, cheer=(11.0, 19.0)),
    "finale": dict(voice=[(0, 6.0), (1, 20.0)], meet=12.0),
}

TAIL = 1.5  # 片尾留白秒数


def build():
    out, t = {}, 0.0
    for name, bpm, bpb, bars in SCENES:
        dur = bars * bpb * 60.0 / bpm
        out[name] = dict(name=name, start=t, bpm=bpm, bpb=bpb, bars=bars, beats=bars * bpb, dur=dur)
        t += dur
    return out, t


TL, TOTAL_SCENES = build()
TOTAL = TOTAL_SCENES + TAIL


def beat_time(scene, beat):
    # 幕内拍号 -> 全片绝对秒数
    s = TL[scene]
    return s["start"] + beat * 60.0 / s["bpm"]


def voice_spans():
    # 每句旁白的 (幕, 句序号, 文本, 起始拍, 结束拍, 绝对起止秒)，用于字幕与混音
    import json
    from pathlib import Path
    meta = json.loads((Path(__file__).resolve().parent.parent / "assets" / "voice" / "voice.json").read_text(encoding="utf-8"))
    out = []
    for sc, cue in CUES.items():
        bpm = TL[sc]["bpm"]
        for i, b0 in cue["voice"]:
            m = meta[sc][i]
            b1 = b0 + m["dur"] * bpm / 60
            out.append(dict(scene=sc, i=i, text=m["text"], b0=b0, b1=b1, t0=beat_time(sc, b0), t1=beat_time(sc, b0) + m["dur"]))
    return out


if __name__ == "__main__":
    for k, v in TL.items():
        print(f"{k:10s} start={v['start']:7.2f}s dur={v['dur']:6.2f}s beats={v['beats']}")
    print("total", TOTAL)
    spans = voice_spans()
    for a, b in zip(spans, spans[1:] + [None]):
        flag = ""
        if a["b1"] > TL[a["scene"]]["beats"]:
            flag += " !!超出本幕"
        if b and b["scene"] == a["scene"] and b["b0"] < a["b1"] + 0.3:
            flag += " !!与下一句重叠"
        print(f"  {a['scene']}_{a['i']} 拍 {a['b0']:5.1f}-{a['b1']:5.1f}{flag}  {a['text']}")
