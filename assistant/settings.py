import json
import math
import os
import uuid
from pathlib import Path


def check_voice_speed(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.5 <= value <= 2.0:
        raise ValueError("음성 출력 속도는 0.5~2.0배로 설정하세요.")
    return round(float(value), 2)


def default_data_dir():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "LunaAssistant"
    return Path.home() / ".local" / "share" / "LunaAssistant"


def load_settings(folder):
    try:
        data = json.loads((Path(folder) / "settings.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        result = {"voice": data.get("voice", "") if isinstance(data.get("voice"), str) else "",
                  "speak": data.get("speak", True) if isinstance(data.get("speak", True), bool) else True}
        if "voice_speed" in data:
            try:
                result["voice_speed"] = check_voice_speed(data["voice_speed"])
            except ValueError:
                pass
        if data.get("ai_mode") in ("off", "local", "cloud"):
            result["ai_mode"] = data["ai_mode"]
        if isinstance(data.get("ollama_model"), str):
            result["ollama_model"] = data["ollama_model"][:120]
        if isinstance(data.get("vision_model"), str):
            result["vision_model"] = data["vision_model"][:120]
        if data.get("search_provider") in ("google", "naver"):
            result["search_provider"] = data["search_provider"]
        if data.get("search_kind") in ("web", "news", "video"):
            result["search_kind"] = data["search_kind"]
        for key in ("remember_chat", "apply_volume_on_start", "windows_control", "wake_enabled", "wake_during_speech"):
            if isinstance(data.get(key), bool):
                result[key] = data[key]
        for key, low, high in (("history_turns", 1, 20), ("volume", 0, 100)):
            if type(data.get(key)) is int and low <= data[key] <= high:
                result[key] = data[key]
        return result
    except (OSError, ValueError):
        return {}


def save_settings(folder, voice, speak, *, ai_mode=None, ollama_model=None, remember_chat=None,
                  history_turns=None, volume=None, apply_volume_on_start=None, windows_control=None,
                  vision_model=None, search_provider=None, search_kind=None, voice_speed=None,
                  wake_enabled=None, wake_during_speech=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f"settings-{uuid.uuid4().hex}.tmp"
    try:
        data = load_settings(folder)
        data.update(voice=voice, speak=bool(speak))
        if voice_speed is not None:
            data["voice_speed"] = check_voice_speed(voice_speed)
        if ai_mode is not None:
            if ai_mode not in {"off", "local", "cloud"}:
                raise ValueError("Invalid AI mode")
            data["ai_mode"] = ai_mode
        if ollama_model is not None:
            data["ollama_model"] = ollama_model
        if vision_model is not None:
            data["vision_model"] = vision_model
        for key, value, allowed in (("search_provider", search_provider, {"google", "naver"}), ("search_kind", search_kind, {"web", "news", "video"})):
            if value is not None:
                if value not in allowed:
                    raise ValueError("Invalid search preference")
                data[key] = value
        for key, value in (("remember_chat", remember_chat), ("apply_volume_on_start", apply_volume_on_start), ("windows_control", windows_control),
                           ("wake_enabled", wake_enabled), ("wake_during_speech", wake_during_speech)):
            if value is not None:
                if type(value) is not bool:
                    raise ValueError("Invalid boolean preference")
                data[key] = value
        for key, value, low, high in (("history_turns", history_turns, 1, 20), ("volume", volume, 0, 100)):
            if value is not None:
                if type(value) is not int or not low <= value <= high:
                    raise ValueError("Invalid numeric preference")
                data[key] = value
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "settings.json")
    finally:
        temporary.unlink(missing_ok=True)
