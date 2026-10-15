# 旁白文稿与合成：每句单独合成，记录时长，供时间轴、字幕与混音使用
import json
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from minimax import tts

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "assets" / "voice"
VOICE_ID = "Chinese (Mandarin)_Gentle_Senior"   # 温柔学姐
SPEED = 0.9
LOG = "[narration 旁白合成]"

# 每幕旁白（按出现顺序）
LINES = {
    "title": ["小马过河！", "小马能过河吗？"],
    "home": ["马棚里，住着一匹小马和他的妈妈。",
             "一天，马妈妈对小马说：",
             "小马，你能把这半口袋麦子，驮到磨坊去吗？",
             "能！小马驮起口袋，高高兴兴地出发了。"],
    "road": ["小马一路小跑，蝴蝶陪着他一起飞。"],
    "river": ["跑着跑着，一条小河挡住了去路。",
              "河上没有桥。河水是深，还是浅呢？",
              "小马低头看看河水，有点犹豫。"],
    "ox": ["这时，老牛伯伯正在河边吃草。",
           "小马跑过去问：牛伯伯，我能过这条河吗？",
           "老牛笑着说：水很浅，才到小腿，能过去。",
           "小马高兴极了，撒开腿就往河边跑。"],
    "squirrel": ["小马！别过河！别过河！",
                 "一只松鼠从树上跳下来，拦住了他。",
                 "河水深得很！我的一个伙伴，就被河水冲走了！",
                 "小马吓了一跳，连忙停住了脚步。"],
    "confused": ["牛伯伯说水很浅，松鼠说水很深，",
                 "到底该听谁的呢？小马没了主意。",
                 "他只好驮着口袋，慢慢地往回走。"],
    "home2": ["妈妈问：孩子，怎么回来啦？",
              "小马难为情地说：河水很深，过不去。",
              "牛伯伯说水很浅，松鼠说水很深，我不知道听谁的。",
              "妈妈蹭蹭小马的头，温柔地说：",
              "牛伯伯那么高大，松鼠那么小，他们说的都不一样呀。",
              "光听别人说可不行，你自己去试一试，就知道啦。",
              "小马点点头，又朝小河跑去。"],
    "back": ["刚到河边，松鼠又跳出来大叫：",
             "小马，别过河！危险！",
             "小马说：谢谢你，松鼠！我自己小心地试一试。"],
    "cross": ["小马抬起前腿，一步一步，小心地走进了河里。",
              "河水凉凉的，刚好到他的膝盖。",
              "小鱼游过来，好像在给他加油。",
              "原来，河水既不像牛伯伯说的那样浅，",
              "也不像松鼠说的那样深。"],
    "mill": ["小马顺利地过了河，把麦子送到了磨坊。",
             "水车吱呀吱呀地转着，小马开心得又蹦又跳。"],
    "finale": ["小朋友，遇到事情，不要只听别人说。",
               "要像小马一样，动动脑筋，自己去试一试哦！"],
}


def wav_dur(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def synth_all(voice=VOICE_ID, out_dir=VOICE_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    items = [(sc, i, txt) for sc, ls in LINES.items() for i, txt in enumerate(ls)]

    def one(it):
        sc, i, txt = it
        p = out_dir / f"{sc}_{i}.wav"
        if not p.exists():
            tts(txt, p, voice, speed=SPEED)
        return sc, i, txt, wav_dur(p)

    meta = {}
    with ThreadPoolExecutor(1) as ex:
        for sc, i, txt, d in ex.map(one, items):
            meta.setdefault(sc, []).append(dict(i=i, text=txt, dur=round(d, 3)))
            print(f"{LOG}[{sc}_{i}] {d:.2f}s {txt}", flush=True)
    for sc in meta:
        meta[sc].sort(key=lambda m: m["i"])
    (out_dir / "voice.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "samples":
        # 试听样本：同一句话用三个候选音色
        d = ROOT / "out" / "voice_samples"
        d.mkdir(parents=True, exist_ok=True)
        text = "马棚里，住着一匹小马和他的妈妈。"
        for vid, nm in [("Chinese (Mandarin)_Gentle_Senior", "温柔学姐"), ("Chinese (Mandarin)_Sweet_Lady", "甜美女声"),
                        ("Chinese (Mandarin)_Warm_Girl", "温暖少女")]:
            tts(text, d / f"{nm}.wav", vid, speed=SPEED)
            print(f"{LOG} 样本 {nm} 完成")
    else:
        synth_all()
