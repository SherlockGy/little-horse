# /// script
# requires-python = ">=3.12"
# dependencies = ["tinysoundfont", "numpy", "scipy"]
# ///
# 小马过河配乐：用 GeneralUser GS 采样音色演奏，全程不用金属打击乐（无钟琴、铃、三角铁、钢片琴等）
import random
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import tinysoundfont as tsf
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, sosfilt

from timeline import CUES, TL, TOTAL, beat_time, voice_spans

ROOT = Path(__file__).resolve().parent.parent
SF2 = ROOT / "assets" / "sf" / "GeneralUser-GS.sf2"
OUT = ROOT / "out" / "music.wav"
VOICE_DIR = ROOT / "assets" / "voice"
SR = 44100
LOG = "[music 配乐生成]"
rng = random.Random(7)

# 声部：GM 音色号、音量、声像(-1 左 ~ 1 右)、混响发送量
PARTS = {
    "flute": dict(prog=73, vol=0.80, pan=0.25, rev=0.32),
    "piccolo": dict(prog=72, vol=0.45, pan=0.30, rev=0.30),
    "clarinet": dict(prog=71, vol=0.85, pan=-0.25, rev=0.30),
    "oboe": dict(prog=68, vol=0.75, pan=0.15, rev=0.30),
    "bassoon": dict(prog=70, vol=0.95, pan=-0.10, rev=0.25),
    "horn": dict(prog=60, vol=0.55, pan=-0.35, rev=0.40),
    "trombone": dict(prog=57, vol=0.70, pan=0.10, rev=0.30),
    "strings": dict(prog=48, vol=0.50, pan=0.0, rev=0.45),
    "trem": dict(prog=44, vol=0.55, pan=0.0, rev=0.45),
    "pizz": dict(prog=45, vol=0.85, pan=-0.15, rev=0.30),
    "bass": dict(prog=45, vol=0.95, pan=0.0, rev=0.20),
    "harp": dict(prog=46, vol=0.75, pan=0.35, rev=0.45),
    "guitar": dict(prog=24, vol=0.55, pan=-0.40, rev=0.30),
    "accordion": dict(prog=21, vol=0.45, pan=-0.20, rev=0.30),
    "timpani": dict(prog=47, vol=0.60, pan=0.0, rev=0.35),
    "drums": dict(prog=0, bank=128, drums=True, vol=0.60, pan=0.0, rev=0.30),
}

NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DUR = {"w": 4, "h": 2, "q": 1, "e": 0.5, "s": 0.25, "t": 0.125}
TOKEN = re.compile(r"^(\[[^\]]+\]|r|[A-G][#b]?-?\d)([whqest])?(\.)?(?::([\d.]+))?$")

CHORDS = {
    "F": "F A C", "C": "C E G", "C7": "C E G Bb", "Dm": "D F A", "Gm": "G Bb D", "Bb": "Bb D F",
    "Am": "A C E", "A7": "A C# E G", "D7": "D F# A C", "Gm7": "G Bb D F", "E7": "E G# B D",
}


def midi(name):
    m = re.match(r"^([A-G])([#b]?)(-?\d)$", name)
    n = NOTE_BASE[m.group(1)] + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)
    return n + (int(m.group(3)) + 1) * 12


def pc(name):
    return midi(name + "4") % 12


def chord_pcs(ch):
    return [pc(x) for x in CHORDS[ch].split()]


def voicing(ch, low, count=3):
    # 从 low 往上取和弦音，得到紧凑排列
    pcs, out, k = chord_pcs(ch), [], low
    while len(out) < count:
        if k % 12 in pcs:
            out.append(k)
        k += 1
    return out


def root(ch, octave):
    return pc(CHORDS[ch].split()[0]) + (octave + 1) * 12


def fifth(ch, octave):
    p = chord_pcs(ch)[2]
    r = root(ch, octave)
    v = p + (octave + 1) * 12
    return v if v > r else v + 12


class Score:
    def __init__(self):
        self.ev = defaultdict(list)

    # ---------- 基础事件 ----------
    def note(self, part, t, dur, key, vel, human=True):
        if human:
            t += rng.uniform(-0.006, 0.006)
            vel = max(1, min(127, int(vel + rng.uniform(-5, 5))))
        self.ev[part].append((max(0.0, t), 1, "on", key, vel))
        self.ev[part].append((max(0.0, t + dur), 0, "off", key, 0))

    def cc(self, part, t, ctrl, val):
        self.ev[part].append((t, 0, "cc", ctrl, int(val)))

    def cc_ramp(self, part, t0, t1, ctrl, v0, v1, steps=24):
        for i in range(steps + 1):
            a = i / steps
            self.cc(part, t0 + (t1 - t0) * a, ctrl, v0 + (v1 - v0) * a)

    def bend(self, part, t0, t1, semis_from, semis_to, rng_semis=12, steps=40):
        # 弯音：用于滑音、狼的惨叫等
        self.ev[part].append((t0 - 0.001, 0, "pbr", rng_semis, 0))
        for i in range(steps + 1):
            a = i / steps
            s = semis_from + (semis_to - semis_from) * a
            self.ev[part].append((t0 + (t1 - t0) * a, 0, "pb", int(8192 + s / rng_semis * 8191), 0))

    # ---------- 按幕内拍号书写 ----------
    def seq(self, part, scene, beat, text, vel=85, legato=0.92, transpose=0):
        bpm = TL[scene]["bpm"]
        for tok in text.split():
            m = TOKEN.match(tok)
            if not m:
                raise ValueError(f"无法解析音符: {tok}")
            pitch, d, dot, num = m.groups()
            dur = float(num) if num else DUR[d or "q"] * (1.5 if dot else 1)
            if pitch != "r":
                keys = [midi(x.strip()) for x in pitch.strip("[]").split(",")] if pitch.startswith("[") else [midi(pitch)]
                t = beat_time(scene, beat)
                for k in keys:
                    self.note(part, t, dur * 60 / bpm * legato, k + transpose, vel)
            beat += dur
        return beat

    def at(self, scene, beat):
        return beat_time(scene, beat)

    # ---------- 伴奏型 ----------
    def oompah(self, bass, chord_part, scene, beat, chords, vel=70, bass_oct=2, low=53, bpb=4):
        bpm = TL[scene]["bpm"]
        spb = 60 / bpm
        for ch, n in chords:
            for i in range(int(n)):
                b = beat + i
                t = beat_time(scene, b)
                pos = round(b) % bpb
                if pos == 0:
                    self.note(bass, t, spb * 0.8, root(ch, bass_oct), vel + 8)
                elif bpb == 4 and pos == 2:
                    self.note(bass, t, spb * 0.8, fifth(ch, bass_oct), vel + 2)
                else:
                    for k in voicing(ch, low):
                        self.note(chord_part, t, spb * 0.45, k, vel - 8)
            beat += n
        return beat

    def arp(self, part, scene, beat, chords, vel=60, low=53, step=0.5, pattern=(0, 1, 2, 3, 2, 1, 0, 1)):
        bpm = TL[scene]["bpm"]
        for ch, n in chords:
            notes = voicing(ch, low, 4)
            for i in range(int(n / step)):
                k = notes[pattern[i % len(pattern)] % len(notes)]
                self.note(part, beat_time(scene, beat + i * step), step * 60 / bpm * 1.6, k, vel)
            beat += n
        return beat

    def pad(self, part, scene, beat, chords, vel=55, low=53, count=3):
        bpm = TL[scene]["bpm"]
        for ch, n in chords:
            for k in voicing(ch, low, count):
                self.note(part, beat_time(scene, beat), n * 60 / bpm * 0.98, k, vel, human=False)
            beat += n
        return beat

    def gliss(self, part, scene, beat, start, end, beats, vel=70, scale=(0, 2, 4, 5, 7, 9, 11), tonic=5):
        # 竖琴/弦乐刮奏：按调内音快速走完
        keys = [k for k in range(min(start, end), max(start, end) + 1) if (k - tonic) % 12 in scale]
        if start > end:
            keys = keys[::-1]
        bpm = TL[scene]["bpm"]
        step = beats / len(keys)
        for i, k in enumerate(keys):
            self.note(part, beat_time(scene, beat + i * step), max(step * 60 / bpm * 3, 0.15), k, vel, human=False)


# ---------------- 主题素材 ----------------
# 小马主题：轻快的小跑节奏（F 大调）
PONY = ["C5e A4e C5e F5e E5q C5q", "D5e Bb4e D5e G5e F5q D5q", "C5e A4e C5e F5e A5q G5e F5e", "E5e D5e C5e Bb4e A4h"]
PONY_END = ["A4e C5e F5q G5e A5e G5q", "F5e E5e D5e E5e F5h"]
CH_PONY = [("F", 4), ("Gm", 2), ("C7", 2), ("F", 2), ("Dm", 2), ("C7", 4)]
CH_PONY_END = [("Bb", 2), ("C7", 2), ("C7", 2), ("F", 2)]
# 妈妈主题：温柔抒情
MOM = ["A4h C5q. Bb4e", "A4h F4h", "G4q. A4e Bb4q D5q", "C5w"]
CH_MOM = [("F", 4), ("Dm", 4), ("Gm", 4), ("C7", 4)]
# 老牛主题：大管慢悠悠
OX = ["F2q. C3e F3q C3q", "Bb2q. F3e Bb3q F3q", "C3q. G3e C4q G3q", "F3h C3h"]
# 松鼠主题：短笛急促的断奏（d 小调）
SQ = ["D6s E6s F6s E6s D6e A5e D6s E6s F6s G6s A6e F6e", "E6s F6s G6s F6s E6e C#6e E6s F6s G6s A6s Bb6e G6e"]


def melody(S, part, scene, beat, bars, vel=90, transpose=0, legato=0.92):
    for b in bars:
        beat = S.seq(part, scene, beat, b, vel, legato, transpose)
    return beat


# ---------------- 音效（非金属）：溪水、涉水、跳鱼、水车吱呀 ----------------
class Sfx:
    def __init__(self, n):
        self.buf = np.zeros((n, 2), np.float32)
        self.nrng = np.random.default_rng(3)

    def add(self, t, sig, gain=1.0, pan=0.0):
        i = int(t * SR)
        if sig.ndim == 1:
            sig = np.stack([sig * (1 - max(0, pan)), sig * (1 + min(0, pan))], 1)
        j = min(len(self.buf), i + len(sig))
        if j > i:
            self.buf[i:j] += sig[: j - i] * gain

    def brook(self, t0, t1, gain=0.08, pan=0.3):
        # 小溪流水：带通噪声 + 随机咕嘟细节，起止淡入淡出
        n = int((t1 - t0) * SR)
        x = np.arange(n) / SR
        out = np.zeros((n, 2), np.float32)
        for ch in range(2):
            y = sosfilt(butter(2, [500, 3200], "bandpass", fs=SR, output="sos"), self.nrng.standard_normal(n))
            y *= 0.75 + 0.25 * np.sin(2 * np.pi * (0.23 + 0.05 * ch) * x + ch)
            out[:, ch] = y
        bub = np.zeros(n)
        for _ in range(int((t1 - t0) * 5)):
            c = self.nrng.uniform(0, t1 - t0)
            f = self.nrng.uniform(500, 1100)
            m = (x > c) & (x < c + 0.08)
            bub[m] += np.sin(2 * np.pi * f * (x[m] - c) * (1 + 3 * (x[m] - c))) * np.exp(-(x[m] - c) / 0.02)
        out += bub[:, None] * 0.25
        env = np.minimum(1, x / 1.0) * np.minimum(1, (x[-1] - x) / 1.0)
        out *= env[:, None] / (np.abs(out).max() + 1e-9)
        self.add(t0, out.astype(np.float32), gain, pan)

    def splash(self, t, gain=0.6, length=1.2):
        n = int(length * SR)
        x = np.arange(n) / SR
        noise = self.nrng.standard_normal(n)
        y = sosfilt(butter(2, [400, 5000], "bandpass", fs=SR, output="sos"), noise) * np.exp(-x / (0.23 * length))
        bub = sum(np.sin(2 * np.pi * f * x * (1 + 0.6 * x)) * np.exp(-(x - d * length) ** 2 / 0.002)
                  for f, d in [(520, 0.15), (700, 0.25), (430, 0.38), (900, 0.5)])
        self.add(t, (y / np.abs(y).max() + 0.35 * bub).astype(np.float32), gain)

    def step(self, t, gain=0.22):
        # 蹄子踩进水里：短促低沉的哗啦
        n = int(0.35 * SR)
        x = np.arange(n) / SR
        y = sosfilt(butter(2, [250, 2500], "bandpass", fs=SR, output="sos"), self.nrng.standard_normal(n)) * np.exp(-x / 0.07)
        y += 0.6 * np.sin(2 * np.pi * 180 * x * (1 - 0.8 * x)) * np.exp(-x / 0.05)
        self.add(t, (y / np.abs(y).max()).astype(np.float32), gain, -0.1)

    def creak(self, t, gain=0.1):
        # 木头水车吱呀：带音高摆动的锯齿波，经带通变成木头摩擦声
        n = int(0.55 * SR)
        x = np.arange(n) / SR
        f = 210 + 60 * np.sin(2 * np.pi * 3.2 * x) + 40 * x
        ph = np.cumsum(f) / SR
        saw = 2 * (ph % 1) - 1
        y = sosfilt(butter(2, [600, 2600], "bandpass", fs=SR, output="sos"), saw)
        env = np.sin(np.pi * np.clip(x / x[-1], 0, 1)) ** 1.5
        self.add(t, (y * env / (np.abs(y).max() + 1e-9)).astype(np.float32), gain, 0.35)

    def whoosh(self, t0, t1, gain=0.18, f0=500, f1=2200):
        # 松鼠跳下来时的嗖一声
        n = int((t1 - t0) * SR)
        noise = self.nrng.standard_normal(n)
        y = np.zeros(n)
        blk = 1024
        for s0 in range(0, n, blk):
            fc = f0 + (f1 - f0) * s0 / n
            seg_ = sosfilt(butter(2, [fc * 0.7, fc * 1.4], "bandpass", fs=SR, output="sos"), noise[max(0, s0 - 256):s0 + blk])
            y[s0:s0 + blk] = seg_[-(min(blk, n - s0)):]
        env = np.sin(np.pi * np.arange(n) / n)
        self.add(t0, (y * env / (np.abs(y).max() + 1e-9)).astype(np.float32), gain)


# ---------------- 各幕编曲 ----------------
def compose():
    S = Score()
    n = int((TOTAL + 2) * SR)
    fx = Sfx(n)

    def trot_bass(sc, b0, b1, chords=("F", "C7"), vel=72):
        # 小跑的低音拨弦：每拍根音、五音交替
        b, i = b0, 0
        while b < b1 - 0.01:
            ch = chords[(i // 4) % len(chords)]
            k = root(ch, 2) if i % 2 == 0 else fifth(ch, 2)
            S.note("bass", S.at(sc, b), 0.25, k, vel)
            b += 1
            i += 1

    def pony_theme(sc, b, part="flute", vel=90):
        e = melody(S, part, sc, b, PONY + PONY_END, vel)
        chords = CH_PONY + CH_PONY_END
        S.oompah("bass", "pizz", sc, b, chords, vel=64)
        S.arp("guitar", sc, b, chords, vel=48, low=55)
        return e

    # ===== 片头：第 0 秒就有明亮的齐奏和奔跑的节奏，抓住前 3 秒 =====
    sc, C = "title", CUES["title"]
    S.seq("strings", sc, 0, "[F3,C4,F4,A4]e", 96)
    S.seq("pizz", sc, 0, "[F2,C3,F3]e", 104)
    S.gliss("harp", sc, 0, midi("F4"), midi("F6"), 0.8, vel=66)
    trot_bass(sc, 0, C["stop"], vel=80)
    S.seq("pizz", sc, 0.5, "F4e A4e C5e A4e F4e", 76)
    for i, k in enumerate(["C5", "F5", "A5", "C6"]):   # 标题字逐个弹出
        S.seq("harp", sc, max(0.0, 0.1 + i * 0.3), f"{k}e", 74)
    melody(S, "flute", sc, 0.5, ["C5e A4e C5e F5e"], 88)
    st = C["stop"]
    S.seq("strings", sc, st, "[F3,C4,F4,A4]q", 100)   # 急停
    S.seq("pizz", sc, st, "[F2,C3]q", 106)
    S.note("timpani", S.at(sc, st), 0.5, midi("F2"), 90, human=False)
    S.gliss("harp", sc, C["voice"][1][1], midi("C5"), midi("C6"), 0.6, vel=60)   # 小马能过河吗？
    S.seq("clarinet", sc, C["voice"][1][1] + 0.4, "A4q C5q D5q", 62)
    S.pad("strings", sc, st + 1, [("Dm", C["ox"] - st - 1)], vel=34, low=55)
    ob = C["ox"]
    melody(S, "bassoon", sc, ob, ["F2q. C3e F3q rq"], 88, legato=0.7)   # 老牛：浅
    S.seq("bassoon", sc, ob + 1.0, "F3e A3e C4q", 84, legato=0.5)
    sb = C["squirrel"]
    fx.whoosh(S.at(sc, sb), S.at(sc, sb + 1.0), gain=0.18)
    S.seq("piccolo", sc, sb + 1.0, SQ[0], 62, legato=0.5)   # 松鼠：深
    S.seq("pizz", sc, sb + 1.0, "D3e A2e D3e A2e", 74)
    S.gliss("harp", sc, 12.0, midi("C4"), midi("C6"), 0.8, vel=55)   # 犯难
    S.seq("clarinet", sc, 12.4, "C5q D5q E5q F#5q", 62)
    S.pad("strings", sc, 12, [("D7", 4)], vel=36, low=54)

    # ===== 马棚 =====
    sc, C = "home", CUES["home"]
    melody(S, "clarinet", sc, 0, MOM, 80)
    S.arp("harp", sc, 0, CH_MOM, vel=48, low=53, pattern=(0, 1, 2, 3, 2, 1))
    S.pad("strings", sc, 0, CH_MOM, vel=36, low=57)
    S.seq("bass", sc, 0, "F2w D2w G2w C2w", 58)
    S.arp("harp", sc, 16, [("F", 4), ("Bb", 4)], vel=38, low=53, step=1.0)   # 妈妈说话：轻轻铺底
    S.pad("strings", sc, 16, [("F", 4), ("Bb", 3)], vel=32, low=57)
    S.seq("pizz", sc, C["nod"], "C5e F5e", 88)   # 能！
    S.pad("strings", sc, 23, [("C7", 1.4)], vel=40, low=57)
    S.gliss("harp", sc, C["load"], midi("F4"), midi("F6"), 0.9, vel=62)   # 口袋飞上背
    S.seq("pizz", sc, C["load"] + 0.9, "[F3,C4,F4]q", 96)
    S.seq("flute", sc, C["load"] + 1.0, "A5e C6e", 70)
    melody(S, "flute", sc, C["go"] + 0.1, PONY[:1], 88)
    S.oompah("bass", "pizz", sc, C["go"] + 0.1, [("F", 4)], vel=62)

    # ===== 路上 =====
    sc = "road"
    pony_theme(sc, 0, "flute", 90)
    melody(S, "clarinet", sc, 8, ["F4h E4h", "D4h C4h"], 60)
    S.seq("strings", sc, 8, "C4e D4e E4e F4e G4e A4e Bb4e C5e", 70)   # 撒欢跑：弦乐上行

    # ===== 小河挡路 =====
    sc, C = "river", CUES["river"]
    melody(S, "flute", sc, 0, ["A5e G5e F5e E5e D5e C5e Bb4e A4e"], 80)
    trot_bass(sc, 0, 4)
    S.seq("strings", sc, C["stop"], "[F3,C4,F4,A4]q", 92)   # 急停
    S.seq("pizz", sc, C["stop"], "[F2,C3]q", 100)
    S.arp("harp", sc, 5, [("Dm", 4), ("Bb", 4), ("F", 4), ("C7", 4), ("Dm", 3)], vel=42, low=57,
          pattern=(0, 1, 2, 3, 2, 1, 0, 1))   # 河水的粼粼琶音
    S.pad("strings", sc, 5, [("Dm", 4), ("Bb", 4), ("F", 4), ("C7", 4), ("Dm", 3)], vel=30, low=50)
    S.seq("clarinet", sc, 9.5, "A4q C5q D5q. rq", 62)   # 是深，还是浅？
    S.seq("clarinet", sc, 13.0, "E5h", 55)
    S.gliss("harp", sc, C["think"] + 0.4, midi("C5"), midi("C6"), 0.6, vel=55)
    S.seq("flute", sc, C["think"] + 1.5, "A5e G5e A5e B5e C6q rq", 58)   # 问号飘起
    S.seq("flute", sc, 21.0, "D6h", 50)

    # ===== 问老牛 =====
    sc, C = "ox", CUES["ox"]
    melody(S, "bassoon", sc, 0, OX[:2], 86, legato=0.7)
    S.seq("pizz", sc, 0, "F2q rq C3q rq Bb2q rq F2q rq", 70)
    S.seq("pizz", sc, C["walk"][0], "C4e D4e E4e F4e G4e A4e", 66)   # 小马跑过去
    S.pad("strings", sc, 9.5, [("F", 3), ("Gm", 3)], vel=32, low=57)
    S.seq("clarinet", sc, 10.4, "C5q D5q E5q G5q", 60)   # 小马问话
    melody(S, "bassoon", sc, 16, OX, 86, legato=0.7)
    S.seq("horn", sc, 16, "[F3,A3]w [D3,F3]w", 40)
    S.seq("bassoon", sc, 22, "F3e A3e C4e A3e F3e A3e C4q", 90, legato=0.5)   # 老牛哈哈笑
    S.seq("trombone", sc, 22.2, "F2e rs A2e rs C3q", 60, legato=0.5)
    S.gliss("harp", sc, C["nod"], midi("F4"), midi("F6"), 1.0, vel=60)
    melody(S, "flute", sc, C["run"][0], PONY[:1], 92)
    S.oompah("bass", "pizz", sc, C["run"][0], [("F", 4)], vel=64)
    S.seq("strings", sc, C["run"][0], "F4e G4e A4e Bb4e C5e D5e E5e F5e", 66)

    # ===== 松鼠拦路 =====
    sc, C = "squirrel", CUES["squirrel"]
    S.seq("piccolo", sc, 0.5, "A6s Bb6s " * 4, 72, legato=0.9)   # 别过河！
    S.seq("trem", sc, 0.5, "[D4,F4,A4]h", 60)
    l0, l1 = C["leap"]
    dm = (0, 2, 3, 5, 7, 8, 10)
    S.gliss("harp", sc, l0, midi("D5"), midi("D7"), (l1 - l0) * 0.5, vel=62, tonic=2, scale=dm)
    S.gliss("harp", sc, l0 + (l1 - l0) * 0.5, midi("D7"), midi("D5"), (l1 - l0) * 0.5, vel=62, tonic=2, scale=dm)
    fx.whoosh(S.at(sc, l0), S.at(sc, l1), gain=0.2)
    S.seq("pizz", sc, l1, "[D3,A3]q", 100)   # 落地
    S.seq("strings", sc, l1, "[A3,D4,F4]e", 85)
    for i in range(4):   # 着急地挥手：短笛断奏 + 弦乐震音
        S.seq("piccolo", sc, 6.0 + i * 2, SQ[i % 2], 62, legato=0.5)
        S.seq("pizz", sc, 6.0 + i * 2, "D3e A2e D3e A2e", 74)
    S.pad("trem", sc, 6, [("Dm", 4), ("A7", 4)], vel=40, low=50)
    S.pad("trem", sc, 14, [("Dm", 3)], vel=38, low=50)
    S.pad("strings", sc, C["sad"], [("Dm", 2), ("Gm", 2), ("A7", 2)], vel=36, low=50)
    S.seq("oboe", sc, C["sad"], "D5h C5q Bb4q A4h", 72)   # 难过地说起伙伴
    st = C["scared"]
    S.seq("trem", sc, st, "[C#4,G4,Bb4]h", 92)   # 吓一跳
    for i in range(6):
        S.note("timpani", S.at(sc, st + i * 0.2), 0.18, midi("A1"), 70 - i * 6)
    S.gliss("flute", sc, st + 0.2, midi("A6"), midi("A4"), 0.8, vel=66, tonic=2, scale=dm)
    S.seq("clarinet", sc, st + 2.5, "A3h G#3h A3w", 52)
    S.seq("bass", sc, st + 2.5, "D2w D2w", 52)

    # ===== 听谁的？ =====
    sc, C = "confused", CUES["confused"]
    S.seq("bassoon", sc, 1.2, "F2q C3q F3h", 80, legato=0.7)   # 老牛说：浅
    S.seq("piccolo", sc, 4.3, SQ[0], 58, legato=0.5)             # 松鼠说：深
    S.seq("pizz", sc, 4.3, "D3e A2e D3e A2e", 66)
    S.gliss("harp", sc, C["think"], midi("C4"), midi("C6"), 1.0, vel=55)
    S.seq("clarinet", sc, C["think"] + 0.5, "C5q D5q E5q F#5q G5h", 64)   # 停在不解决的音上
    S.pad("strings", sc, C["think"] + 0.5, [("D7", 4)], vel=34, low=54)
    S.seq("clarinet", sc, C["sad"], "A4h G4h F4h E4h D4w", 60)   # 慢慢往回走
    S.pad("strings", sc, C["sad"], [("Dm", 4), ("Gm", 4), ("A7", 4)], vel=30, low=50)
    for i in range(int(C["leave"][1] + 1 - C["leave"][0])):
        S.seq("pizz", sc, C["leave"][0] + 0.6 + i, "D3e" if i % 2 == 0 else "A2e", 52)

    # ===== 回家问妈妈 =====
    sc, C = "home2", CUES["home2"]
    for i in range(5):
        S.seq("pizz", sc, i, "A2e" if i % 2 else "D3e", 50)
    S.pad("strings", sc, 2, [("Dm", 6), ("Bb", 6), ("Gm", 6), ("A7", 6)], vel=28, low=55)
    S.arp("harp", sc, 2, [("Dm", 6), ("Bb", 6), ("Gm", 6), ("A7", 6)], vel=34, low=53, step=1.0)
    nb = C["nuzzle"][0] - 1
    melody(S, "clarinet", sc, nb, MOM[:2], 78)   # 妈妈蹭蹭头：温柔主题
    S.pad("strings", sc, nb, [("F", 4), ("Dm", 4)], vel=36, low=57)
    S.arp("harp", sc, nb + 8, [("Gm", 4), ("C7", 4), ("F", 4), ("Dm", 4)], vel=36, low=53, step=1.0)
    S.pad("strings", sc, nb + 8, [("Gm", 4), ("C7", 4), ("F", 4), ("Dm", 4)], vel=30, low=57)
    S.seq("flute", sc, 44, "C5q D5q E5q F5q", 58)   # 想明白了
    S.pad("strings", sc, 44, [("C7", 5)], vel=38, low=55)
    S.seq("pizz", sc, C["nod"], "C5e F5e", 88)
    melody(S, "flute", sc, C["cheer"], ["C5e A4e C5e F5e A5q rq"], 88)
    S.seq("pizz", sc, C["cheer"], "F3e C4e F3e C4e A3e rq", 70)
    S.seq("strings", sc, C["go"][0], "C4e D4e E4e F4e G4e A4e Bb4e C5e", 72)
    S.gliss("harp", sc, C["go"][0] + 2, midi("F4"), midi("F6"), 1.0, vel=60)

    # ===== 回到河边 =====
    sc, C = "back", CUES["back"]
    melody(S, "flute", sc, 0, PONY[:1], 88)
    S.oompah("bass", "pizz", sc, 0, [("F", 4)], vel=62)
    S.seq("piccolo", sc, C["pop"], SQ[0], 60, legato=0.5)   # 松鼠又跳出来
    S.seq("piccolo", sc, C["pop"] + 2, SQ[1], 60, legato=0.5)
    S.seq("pizz", sc, C["pop"], "D3e A2e D3e A2e " * 3, 66)
    S.pad("trem", sc, C["pop"], [("Dm", 6)], vel=36, low=50)
    S.pad("strings", sc, 12, [("F", 4), ("Bb", 4)], vel=34, low=57)   # 小马温和地回答
    S.seq("clarinet", sc, 13, "A4h C5h D5h C5h", 62)
    S.gliss("harp", sc, C["aside"], midi("C5"), midi("C7"), 0.6, vel=60)
    S.gliss("harp", sc, C["aside"] + 0.6, midi("C7"), midi("C5"), 0.6, vel=60)
    fx.whoosh(S.at(sc, C["aside"]), S.at(sc, C["aside"] + 1.2), gain=0.15)
    S.seq("pizz", sc, C["edge"][0], "C4q D4q E4q", 66)   # 小心地走到岸边
    S.seq("clarinet", sc, 23, "F4h", 50)

    # ===== 小心过河 =====
    sc, C = "cross", CUES["cross"]
    CH_X = [("F", 4), ("Bb", 4), ("F", 4), ("C7", 4), ("Dm", 4), ("Bb", 4)]
    S.pad("strings", sc, 0, CH_X, vel=32, low=55)
    S.arp("harp", sc, 0, CH_X, vel=40, low=60, step=0.5, pattern=(0, 2, 1, 3, 2, 4, 3, 1))   # 粼粼水波
    S.seq("clarinet", sc, C["step"], "C5h.", 55)
    fx.step(S.at(sc, C["down"]), 0.3)
    fx.splash(S.at(sc, C["down"]), 0.12, 0.6)
    w0, w1 = C["wade"]
    nst = max(1, round((w1 - w0) * 0.9))
    steps_scale = ["F3", "G3", "A3", "Bb3", "C4", "D4", "E4", "F4", "G4", "A4"]
    for j in range(1, nst + 1):   # 一步一个拨弦，一步一声水响
        tb = w0 + j * (w1 - w0) / nst
        S.seq("pizz", sc, tb, f"{steps_scale[min(j // 2, len(steps_scale) - 1)]}e", 70)
        fx.step(S.at(sc, tb), 0.2)
    melody(S, "clarinet", sc, C["voice"][1][1], ["A4q C5q F5h", "E5q D5q C5h"], 66)   # 凉凉的河水
    f0, _ = C["fish"]
    for fb in (f0 + 0.3, f0 + 2.2, f0 + 3.6):   # 小鱼跳出水面
        S.seq("flute", sc, fb, "C6s D6s E6s F6s G6e", 66)
        fx.splash(S.at(sc, fb), 0.12, 0.6)
        fx.splash(S.at(sc, fb + 1.3), 0.14, 0.7)
    S.pad("strings", sc, 20.5, [("Bb", 2), ("C7", 2), ("F", 3), ("Bb", 2), ("C7", 3)], vel=46, low=57, count=4)   # 原来如此
    S.seq("horn", sc, 20.5, "[F3,A3]h [E3,G3]h [F3,A3]h", 48)
    o0, o1 = C["out"]
    S.seq("pizz", sc, 23.5, "C5e F5e", 80)   # 点点头：原来如此
    melody(S, "flute", sc, o0, ["C5e A4e C5e F5e A5q C6q"], 92)
    melody(S, "oboe", sc, o0, ["C5e A4e C5e F5e A5q C6q"], 70, transpose=-12)
    S.oompah("bass", "pizz", sc, o0, [("F", 4)], vel=66)
    for j in range(1, round((o1 - o0) * 2) + 1):
        fx.step(S.at(sc, o0 + j * 0.5), 0.16)
    fx.brook(S.at(sc, 0) - 0.3, S.at(sc, 32) + 0.3, gain=0.13, pan=0.0)

    # ===== 磨坊 =====
    sc, C = "mill", CUES["mill"]
    melody(S, "accordion", sc, 0, PONY, 82)
    melody(S, "flute", sc, 8, PONY[2:], 70, transpose=12)
    S.oompah("bass", "pizz", sc, 0, CH_PONY, vel=62)
    S.seq("pizz", sc, C["unload"] + 0.9, "[F2,C3,F3]q", 98)   # 口袋落地
    S.gliss("harp", sc, C["unload"], midi("F6"), midi("F4"), 0.9, vel=55)
    melody(S, "flute", sc, 16, ["C6e A5e F5e A5e C6q rq"], 80)
    S.oompah("bass", "pizz", sc, 16, [("F", 4)], vel=66)
    S.gliss("harp", sc, 18.5, midi("F4"), midi("F6"), 1.0, vel=58)
    for k in range(9):   # 水车吱呀吱呀
        fx.creak(S.at(sc, 0.5 + k * 2.2), 0.09)
    fx.brook(S.at(sc, 0) - 0.3, S.at(sc, 20) + 0.3, gain=0.06, pan=0.5)

    # ===== 圆舞曲结尾 =====
    sc = "finale"
    Wm = ["C5h A4q", "F5h E5q", "D5q C5q Bb4q", "A4h.", "G4h A4q", "Bb4h D5q", "C5q Bb4q A4q", "G4h.",
          "C5h A4q", "F5h E5q", "D5q F5q D5q", "C5h A4q", "Bb4q D5q Bb4q", "A4q C5q A4q", "G4q A4q E4q", "F4h."]
    WC = ["F", "F", "Bb", "F", "C7", "Gm", "C7", "C7", "F", "F", "Bb", "F", "Gm", "F", "C7", "F"]
    melody(S, "accordion", sc, 0, Wm, 78)
    melody(S, "flute", sc, 0, Wm[:8], 70)
    melody(S, "flute", sc, 24, Wm[8:], 80, transpose=12)
    S.seq("clarinet", sc, 24, "A4h. A4h. F4h. A4h. G4h. F4h. E4h. A4h.", 58)
    S.oompah("bass", "pizz", sc, 0, [(c, 3) for c in WC], vel=62, bpb=3, low=55)
    S.pad("strings", sc, 24, [(c, 3) for c in WC[8:]], vel=42, low=60)
    S.arp("harp", sc, 48, [("F", 3), ("Bb", 3), ("F", 6)], vel=58, low=53, step=0.5, pattern=(0, 1, 2, 3, 4, 5))
    S.pad("strings", sc, 48, [("F", 12)], vel=48, low=53, count=4)
    S.seq("flute", sc, 48, "A5h. C6h. F6:6", 58)
    S.seq("bass", sc, 48, "F2h. Bb1h. F1:6", 66)
    S.cc_ramp("strings", S.at(sc, 54), S.at(sc, 60) + 1.4, 11, 127, 20)
    S.cc_ramp("flute", S.at(sc, 54), S.at(sc, 60) + 1.4, 11, 127, 20)

    # 河边几幕的流水环境声
    for name in ("river", "ox", "squirrel", "confused", "back"):
        st = TL[name]["start"]
        fx.brook(st + (1.5 if name == "river" else -0.3), st + TL[name]["dur"] + 0.3, gain=0.07)
    return S, fx, n


def render_part(name, spec, events, n, sfbytes):
    syn = tsf.Synth(samplerate=SR)
    sfid = syn.sfload(sfbytes)
    syn.program_select(0, sfid, spec.get("bank", 0), spec["prog"], spec.get("drums", False))
    out = np.zeros((n, 2), np.float32)
    pos = 0
    for t, _, kind, a, b in sorted(events, key=lambda e: (e[0], e[1])):
        s = min(n, int(t * SR))
        if s > pos:
            out[pos:s] = np.frombuffer(syn.generate_simple(s - pos), np.float32).reshape(-1, 2)
            pos = s
        if kind == "on":
            syn.noteon(0, a, b)
        elif kind == "off":
            syn.noteoff(0, a)
        elif kind == "cc":
            syn.control_change(0, a, b)
        elif kind == "pb":
            syn.pitchbend(0, a)
        elif kind == "pbr":
            syn.pitchbend_range(0, a)
    if pos < n:
        out[pos:] = np.frombuffer(syn.generate_simple(n - pos), np.float32).reshape(-1, 2)
    return out


def reverb_ir(seconds=2.4):
    # 合成混响脉冲：两声道独立衰减噪声 + 低通，模拟温暖的小音乐厅
    m = int(seconds * SR)
    g = np.random.default_rng(11)
    x = np.arange(m) / SR
    ir = np.zeros((m, 2))
    lp = butter(1, 5000, "lowpass", fs=SR, output="sos")
    for c in range(2):
        noise = sosfilt(lp, g.standard_normal(m))
        ir[:, c] = noise * np.exp(-x * 6.9 / 1.9)
    ir[: int(0.02 * SR)] = 0  # 预延迟
    return ir / np.sqrt((ir ** 2).sum(0))


def main():
    S, fx, n = compose()
    sfbytes = SF2.read_bytes()
    dry = np.zeros((n, 2), np.float32)
    send = np.zeros((n, 2), np.float32)
    for name, evs in S.ev.items():
        spec = PARTS[name]
        y = render_part(name, spec, evs, n, sfbytes)
        p = spec["pan"]
        y[:, 0] *= np.sqrt((1 - p) / 2) * 1.414
        y[:, 1] *= np.sqrt((1 + p) / 2) * 1.414
        y *= spec["vol"]
        dry += y
        send += y * spec["rev"]
        print(f"{LOG}[{name}] 渲染完成，事件数={len(evs)}", flush=True)
    ir = reverb_ir()
    wet = np.stack([fftconvolve(send[:, c], ir[:, c])[:n] for c in range(2)], 1).astype(np.float32)
    mix = dry + wet * 0.9 + fx.buf
    mix = sosfilt(butter(2, 35, "highpass", fs=SR, output="sos"), mix, axis=0)

    # 母带：归一化到 -1.5 dBFS 并做软限幅
    mix = mix / (np.percentile(np.abs(mix), 99.95) + 1e-9) * 0.7
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    mix *= 10 ** (-1.5 / 20) / max(1e-9, np.abs(mix).max())

    # 旁白：放到对应时间点；有人声时背景音乐自动压低（约 -8 dB），前后留 0.25s/0.45s 过渡
    voice = np.zeros(len(mix), np.float32)
    mark = np.zeros(len(mix), np.float32)
    for sp in voice_spans():
        sr_v, v = wavfile.read(VOICE_DIR / f"{sp['scene']}_{sp['i']}.wav")
        v = v.astype(np.float32)
        if v.ndim > 1:
            v = v.mean(1)
        if np.abs(v).max() > 2:
            v /= 32768.0
        if sr_v != SR:
            v = np.interp(np.arange(int(len(v) * SR / sr_v)) * sr_v / SR, np.arange(len(v)), v).astype(np.float32)
        i0 = int(sp["t0"] * SR)
        j = min(len(voice), i0 + len(v))
        voice[i0:j] += v[: j - i0]
        mark[max(0, i0 - int(0.25 * SR)):min(len(mix), j + int(0.45 * SR))] = 1
    k = int(0.2 * SR)
    env = 1 - 0.62 * np.convolve(mark, np.ones(k) / k, mode="same")
    voice = sosfilt(butter(2, 80, "highpass", fs=SR, output="sos"), voice)
    voice *= 0.82 / (np.percentile(np.abs(voice[np.abs(voice) > 1e-4]), 99.9) + 1e-9)
    mix = mix * env[:, None] * 0.92 + voice[:, None] * 0.95
    mix = np.tanh(mix * 1.05) / np.tanh(1.05)
    mix *= 10 ** (-1.0 / 20) / max(1e-9, np.abs(mix).max())
    fade = int(1.0 * SR)
    end = int(TOTAL * SR)
    mix[end - fade:end] *= np.linspace(1, 0, fade)[:, None]
    mix[end:] = 0
    mix = mix[:end]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(OUT, SR, (mix * 32767).astype(np.int16))
    print(f"{LOG} 输出 {OUT}，时长 {len(mix) / SR:.1f}s")


if __name__ == "__main__":
    main()
