# 批量调用 codex CLI 生成《小马过河》纸艺风格素材（并发执行，已存在的文件跳过）
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "raw"
LOG = "[gen_assets 素材生成]"

# 统一画风描述：纸艺剪纸 + 毛毡质感的绘本风格（与《三只小猪》一致）
STYLE = (
    "Handcrafted layered paper-cut and felt craft style, like a stop-motion paper puppet film: "
    "cut colored cardstock, felt and watercolor-textured paper pieces stacked in layers with subtle depth, "
    "visible paper fibers, soft deckled cut edges. Warm, gentle pastel picture-book palette "
    "(cream, butter yellow, sage and spring green, sky blue, soft coral pink). Cozy, friendly, "
    "for young children. Award-winning picture book quality."
)


def cutout_suffix(key):
    # 抠图用纯色背景：绿色或品红
    hexv = "#00FF00" if key == "green" else "#FF00FF"
    name = "green" if key == "green" else "magenta"
    return (
        " Centered with generous empty margin on all sides, the whole figure fully visible and not cropped, "
        "a thin white paper-cut border around the silhouette, no cast shadow, no ground, no text. "
        f"Background: perfectly flat solid pure {name} ({hexv}) with nothing else on it."
    )


BG_SUFFIX = (
    " Wide landscape composition (3:2), a layered paper diorama with soft depth between layers. "
    "No characters, no animals, no people, no text, no border, no frame."
)

# 角色设定
PONY = ("a small cute young pony foal with a soft caramel-brown felt coat, a cream-colored muzzle and a cream star on the forehead, "
        "a fluffy dark-chocolate mane and tail, big shiny friendly eyes, slender legs with little dark hooves, "
        "a small red knitted scarf around the neck")
MOM = ("a gentle loving mother horse, taller and more graceful than a foal, warm chestnut-brown felt coat, cream muzzle, "
       "a long flowing cream-colored mane with a small daisy tucked in it, a blue gingham neckerchief")
OX = ("a big kind old ox like a friendly grandpa, stocky round body, soft grey-brown felt coat, short curved cream horns, "
      "droopy kind eyes, a little round woven straw hat between the horns, chewing a long stalk of grass")
SQUIRREL = ("a small lively red squirrel, bright orange felt fur with a cream belly, a huge fluffy curled tail, "
            "tufted ears, big round worried-looking eyes")


def ch(desc, pose, key="green"):
    return (f"A single character for a children's animated film, made as a handcrafted paper-cut puppet: {desc}. "
            f"{pose} {STYLE}" + cutout_suffix(key))


def ref_ch(pose, key="green"):
    # 带参考图的姿势生成：要求角色设计完全一致
    return ("Use the attached image as the exact character reference. Generate a NEW image of the SAME character, "
            "keeping the identical design, face, colors, clothing, proportions and paper-cut felt craft style. "
            f"New pose: {pose} {STYLE}" + cutout_suffix(key))


def prop(desc, key="green"):
    return f"A single handcrafted paper-cut prop for a children's animated film: {desc}. {STYLE}" + cutout_suffix(key)


JOBS = {
    # ---------- 第一批：角色基准姿势（全部侧面朝右，四条腿着地） ----------
    "pony_stand": dict(prompt=ch(PONY, "Full body, standing on all four legs, clear side view facing right with the head turned slightly toward the viewer, curious happy smile.")),
    "mom_stand": dict(prompt=ch(MOM, "Full body, standing on all four legs, clear side view facing right with the head turned slightly toward the viewer, warm loving smile.")),
    "ox_stand": dict(prompt=ch(OX, "Full body, standing on all four legs, clear side view facing right with the head turned toward the viewer, calm friendly smile.")),
    "squirrel_stand": dict(prompt=ch(SQUIRREL, "Full body, standing upright on hind legs, three-quarter view facing right, tiny paws held in front of the chest, tail curled up behind.")),
    # ---------- 背景 ----------
    "bg_home": dict(prompt="A cozy farm in the morning: a small red wooden horse stable with a hay loft on the far left side, a wooden fence, a hay pile, rolling green paper hills, a dirt path leading to the right, wildflowers, a soft paper sun, fluffy paper clouds. A wide flat open grassy ground across the center and right lower half for characters. " + STYLE + BG_SUFFIX),
    "bg_road": dict(prompt="A sunny meadow with a winding dirt path running left to right, rolling hills with patchwork fields, a few round trees, wildflowers, blue sky with paper clouds. A wide flat open grassy ground across the lower half for characters. " + STYLE + BG_SUFFIX),
    "bg_river": dict(prompt="A riverside meadow seen from the side: the left 60 percent of the image is a wide flat grassy river bank with a big leafy oak tree at the far left edge that has one thick low horizontal branch; the right 40 percent is a gentle clear blue river flowing from the distant hills toward the viewer, with a far grassy bank beyond it and a tiny watermill in the far distance on the far bank. No bridge. A wide flat open grassy ground across the center-left lower half for characters. " + STYLE + BG_SUFFIX),
    "bg_ford": dict(prompt="A gentle wide river seen from the side: calm blue felt water with soft ripples fills the lower 40 percent of the image across the full width, a few reeds and water lilies at the left and right edges, the far grassy bank with willow trees behind the river, distant hills and blue sky. No bridge, no boats. " + STYLE + BG_SUFFIX),
    "bg_millside": dict(prompt="A sunny riverside meadow on the far bank of a river: a small stream on the right side, flowers, round trees, distant hills, blue sky with paper clouds. A wide flat open grassy ground across the center and lower half where a building could stand. No buildings. " + STYLE + BG_SUFFIX),
    "bg_sunset": dict(prompt="A meadow with a winding dirt path at golden sunset: warm orange, peach and lavender paper sky, a big soft setting sun near the horizon, rolling hills in silhouette layers, a few round trees, a little river glinting far away. A wide flat open grassy ground across the lower half for characters. " + STYLE + BG_SUFFIX),
    # ---------- 建筑与道具 ----------
    "mill": dict(prompt=prop("a cute small watermill cottage made of round stones and timber with a thatched roof, a little wooden door with a white flour sack beside it, one small window, front three-quarter view. On its right side wall there is only a round wooden axle hub; the waterwheel itself is NOT drawn (it is a separate piece)")),
    "waterwheel": dict(prompt=prop("one big wooden waterwheel with many paddles, seen perfectly straight from the front as a flat circle, symmetric, with a round hub in the exact center")),
    "fg_water": dict(prompt="A long horizontal border strip of paper-cut felt river water: layered blue and teal felt waves with small white foam curls and ripples, spanning the full width of the image along the bottom, about one third of the image height, the top edge is a soft wavy water line. " + STYLE + " Everything above the strip is perfectly flat solid pure magenta (#FF00FF). No text."),
    "props": dict(prompt="A sheet of separate handcrafted paper-cut props arranged in a loose grid of 2 rows and 4 columns with large empty space between every item, no item touching another: a half-full burlap sack of wheat tied with a rope, a small white flour sack, a small orange fish, a small blue fish, a blue dragonfly with open wings, a green lily pad with a pink water-lily flower, a brown acorn, a bundle of golden wheat ears. " + STYLE + " Each item has a thin white paper-cut border. Background: perfectly flat solid pure magenta (#FF00FF) with nothing else on it, no text."),
}

# 第二批：基于参考图的姿势
JOBS_REF = {
    "pony_sack": dict(ref="pony_stand", prompt=ref_ch("Exactly the same standing pose and side view facing right, but now a half-full burlap sack of wheat tied with a rope is carried across the pony's back, cheerful proud smile.")),
    "squirrel_warn": dict(ref="squirrel_stand", prompt=ref_ch("Full body, three-quarter view facing right, jumping up in alarm with both little arms raised high and spread, mouth wide open shouting a warning, eyebrows raised in worry, tail puffed up.")),
}


def edit(change):
    # 精修动作帧：在参考图上只改动局部，保证与原图逐帧对齐
    return ("Edit the attached image to make the next frame of a frame-by-frame animation. Keep EVERYTHING else exactly "
            "the same as the attached image: the same character design, face, colors, clothing, proportions, size, "
            "position in the frame, camera angle, paper-cut felt craft style, white paper border, and the perfectly flat "
            f"solid pure green (#00FF00) background. Only change this: {change} "
            "No text, no shadow, no ground, nothing else added.")


# 第三批：动作帧（参考图为原始绿底图）
JOBS_FRAMES = {
    # 小马（驮口袋，全片主要姿势）
    "pony_blink": dict(ref="pony_sack", prompt=edit("the pony's eyes are gently closed in a happy blink (curved closed eyelids), everything else identical.")),
    "pony_walka": dict(ref="pony_sack", prompt=edit("the pony is mid-stride trotting to the right: the near front leg lifted and bent forward, the near hind leg pushed back, the far legs in the opposite position, mane and tail bouncing slightly. Head, body and sack unchanged.")),
    "pony_walkb": dict(ref="pony_sack", prompt=edit("the pony is mid-stride trotting to the right: the far front leg lifted and bent forward, the far hind leg pushed back, the near legs planted under the body, mane and tail bouncing slightly the other way. Head, body and sack unchanged.")),
    "pony_gallop": dict(ref="pony_sack", prompt=edit("the pony is galloping happily to the right in a leap: both front legs tucked up forward, both hind legs stretched back, body slightly tilted up, mane and tail flying, big joyful smile. The sack stays on the back.")),
    "pony_worry": dict(ref="pony_sack", prompt=edit("the pony is hesitant and unsure: head lowered and stretched forward looking down to the right at the ground, ears tilted back, worried eyebrows, small uncertain mouth. Legs and sack unchanged.")),
    "pony_ask": dict(ref="pony_sack", prompt=edit("the pony looks up to the right and politely asks a question: head raised a little, mouth open as if speaking, ears up, eyebrows raised hopefully. Legs and sack unchanged.")),
    "pony_scared": dict(ref="pony_sack", prompt=edit("the pony is startled and jumps back: front legs lifted off the ground and pulled back, body leaning back, eyes wide open in surprise, ears straight up, mane flicked. The sack stays on the back.")),
    "pony_sad": dict(ref="pony_sack", prompt=edit("the pony is sad and confused: head hanging low, ears drooping down, eyes looking down, small frown, tail hanging down. Legs and sack unchanged.")),
    "pony_think": dict(ref="pony_sack", prompt=edit("the pony is thinking hard: head tilted to one side, eyes looking up, one front hoof slightly raised and bent, puzzled mouth. Sack unchanged.")),
    "pony_nod": dict(ref="pony_sack", prompt=edit("the pony nods eagerly: head dipped down and forward, eyes closed in a bright happy smile, ears forward. Legs and sack unchanged.")),
    "pony_step": dict(ref="pony_sack", prompt=edit("the pony carefully tests the way: the near front leg lifted high and stretched forward to the right with the hoof pointed down, about to step down, body leaning back cautiously, focused brave face with the tongue slightly out. Sack unchanged.")),
    "pony_cheer": dict(ref="pony_sack", prompt=edit("the pony rears up joyfully on the hind legs with both front legs raised and bent, head thrown up with a huge happy open-mouth smile, eyes closed with joy, mane and tail flying. The sack stays on the back.")),
    # 小马（还没驮口袋，只在开头用）
    "pony_nblink": dict(ref="pony_stand", prompt=edit("the pony's eyes are gently closed in a happy blink (curved closed eyelids), everything else identical.")),
    "pony_nwalka": dict(ref="pony_stand", prompt=edit("the pony is mid-stride trotting to the right: the near front leg lifted and bent forward, the near hind leg pushed back, the far legs in the opposite position, mane and tail bouncing slightly. Head and body unchanged.")),
    "pony_nwalkb": dict(ref="pony_stand", prompt=edit("the pony is mid-stride trotting to the right: the far front leg lifted and bent forward, the far hind leg pushed back, the near legs planted under the body, mane and tail bouncing slightly the other way. Head and body unchanged.")),
    "pony_ncheer": dict(ref="pony_stand", prompt=edit("the pony rears up joyfully on the hind legs with both front legs raised and bent, head thrown up with a huge happy open-mouth smile, eyes closed with joy, mane and tail flying.")),
    "pony_nnod": dict(ref="pony_stand", prompt=edit("the pony nods eagerly: head dipped down and forward, eyes closed in a bright happy smile, ears forward. Legs unchanged.")),
    # 马妈妈
    "mom_blink": dict(ref="mom_stand", prompt=edit("the mother horse's eyes are gently closed in a warm loving smile, everything else identical.")),
    "mom_talk": dict(ref="mom_stand", prompt=edit("the mother horse is gently speaking: mouth open in a soft smile as if talking, head tilted down a little toward a small foal in front of her. Legs unchanged.")),
    "mom_nuzzle": dict(ref="mom_stand", prompt=edit("the mother horse lowers her head and neck far down and forward to the right, as if lovingly nuzzling a small foal, eyes closed, tender smile. Legs unchanged.")),
    # 老牛
    "ox_blink": dict(ref="ox_stand", prompt=edit("the ox's eyes are closed in a calm content blink, everything else identical.")),
    "ox_talk": dict(ref="ox_stand", prompt=edit("the ox is talking with a big friendly smile, mouth open, the grass stalk tilted up, eyebrows raised kindly. Legs unchanged.")),
    "ox_laugh": dict(ref="ox_stand", prompt=edit("the ox laughs heartily: head tipped back a little, eyes squeezed shut, mouth wide open in a big grin, the grass stalk bobbing. Legs unchanged.")),
    "ox_chew": dict(ref="ox_stand", prompt=edit("the ox is chewing: cheeks puffed sideways, the grass stalk shifted to the other side of the mouth, eyes half closed and relaxed. Everything else identical.")),
    # 松鼠
    "squirrel_blink": dict(ref="squirrel_stand", prompt=edit("the squirrel's eyes are closed in a quick blink, everything else identical.")),
    "squirrel_warn2": dict(ref="squirrel_warn", prompt=edit("the arms are now waved in the other position (one arm up, one arm out to the side), the next frame of frantic waving, still shouting. Everything else identical.")),
    "squirrel_sad": dict(ref="squirrel_stand", prompt=edit("the squirrel is sad remembering something: paws clasped together at the chest, eyes looking down with a tiny tear, ears drooping, tail lowered. Legs unchanged.")),
    "squirrel_wave": dict(ref="squirrel_stand", prompt=edit("the squirrel waves goodbye cheerfully with one paw raised high, big friendly smile. Everything else identical.")),
}


def run_job(name, spec):
    out = RAW / f"{name}.png"
    if out.exists():
        return name, "skip"
    prompt = (
        "Use your image generation tool to create exactly ONE image from the image prompt below, "
        f"then copy the resulting PNG file into the current working directory as {name}.png. "
        "Do not do anything else and do not create any other files.\n\nImage prompt:\n" + spec["prompt"]
    )
    cmd = ["codex.cmd", "exec", "--skip-git-repo-check", "-s", "workspace-write"]
    if spec.get("ref"):
        cmd += ["-i", str(RAW / f"{spec['ref']}.png")]
    # 提示词通过 stdin 传入，避免 .cmd 包装截断多行参数
    cmd += ["-"]
    t = time.time()
    r = None
    for attempt in range(2):
        r = subprocess.run(cmd, cwd=RAW, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if out.exists():
            return name, f"ok {time.time() - t:.0f}s"
    return name, "FAIL: " + (r.stdout or "")[-400:] + (r.stderr or "")[-400:]


def main():
    batch = sys.argv[1] if len(sys.argv) > 1 else "a"
    only = set(sys.argv[2:])
    jobs = {"a": JOBS, "b": JOBS_REF, "c": JOBS_FRAMES}[batch]
    items = [(k, v) for k, v in jobs.items() if not only or k in only]
    with ThreadPoolExecutor(max_workers=7) as ex:
        for name, status in ex.map(lambda kv: run_job(*kv), items):
            print(f"{LOG}[{name}] {status}", flush=True)


if __name__ == "__main__":
    main()
