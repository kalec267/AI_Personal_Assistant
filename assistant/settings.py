import json
import os
import uuid
from pathlib import Path


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
        if data.get("ai_mode") in ("off", "local", "cloud"):
            result["ai_mode"] = data["ai_mode"]
        if isinstance(data.get("ollama_model"), str):
            result["ollama_model"] = data["ollama_model"][:120]
        return result
    except (OSError, ValueError):
        return {}


def save_settings(folder, voice, speak, *, ai_mode=None, ollama_model=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f"settings-{uuid.uuid4().hex}.tmp"
    try:
        data = load_settings(folder)
        data.update(voice=voice, speak=bool(speak))
        if ai_mode is not None:
            if ai_mode not in {"off", "local", "cloud"}:
                raise ValueError("Invalid AI mode")
            data["ai_mode"] = ai_mode
        if ollama_model is not None:
            data["ollama_model"] = ollama_model
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "settings.json")
    finally:
        temporary.unlink(missing_ok=True)
