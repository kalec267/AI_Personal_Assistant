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
        return {"voice": data.get("voice", "") if isinstance(data.get("voice"), str) else "",
                "speak": data.get("speak", True) if isinstance(data.get("speak", True), bool) else True}
    except (OSError, ValueError):
        return {}


def save_settings(folder, voice, speak):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f"settings-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"voice": voice, "speak": bool(speak)}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "settings.json")
    finally:
        temporary.unlink(missing_ok=True)
