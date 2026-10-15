# MiniMax 语音合成封装：读取密钥、查询音色、合成旁白
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = "[minimax 旁白配音]"


def _env(name, default=None):
    # 优先进程环境变量，其次 .env 文件，最后 Windows 用户级环境变量（setx 后无需重启）
    if os.environ.get(name):
        return os.environ[name]
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"')
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            pass
    return default


API_KEY = _env("MINIMAX_API_KEY")
API_BASE = _env("MINIMAX_API_BASE", "https://api.minimax.cn").rstrip("/")


def _post(path, body):
    req = urllib.request.Request(API_BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def list_voices():
    return _post("/v1/get_voice", {"voice_type": "system"})


def tts(text, out_path, voice_id, speed=0.92, pitch=0, emotion=None, model="speech-2.8-hd"):
    vs = {"voice_id": voice_id, "speed": speed, "vol": 1, "pitch": pitch}
    if emotion:
        vs["emotion"] = emotion
    body = {"model": model, "text": text, "stream": False, "voice_setting": vs, "language_boost": "Chinese",
            "audio_setting": {"sample_rate": 44100, "format": "wav", "channel": 1}}
    # 限流（1002）时退避重试
    for attempt in range(8):
        res = _post("/v1/t2a_v2", body)
        if res.get("base_resp", {}).get("status_code") != 1002:
            break
        time.sleep(8 + attempt * 6)
    if res.get("base_resp", {}).get("status_code") != 0:
        raise RuntimeError(f"{LOG} 合成失败: {res.get('base_resp')}")
    Path(out_path).write_bytes(bytes.fromhex(res["data"]["audio"]))
    return res.get("extra_info", {})


if __name__ == "__main__":
    if sys.argv[1] == "voices":
        res = list_voices()
        print(json.dumps(res.get("base_resp"), ensure_ascii=False))
        for v in res.get("system_voice", []):
            print(v.get("voice_id"), "|", v.get("voice_name"), "|", (v.get("description") or [""])[0] if isinstance(v.get("description"), list) else v.get("description"))
