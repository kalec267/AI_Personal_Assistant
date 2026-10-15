"""Only user-registered executables can be launched by the model."""
import json
import subprocess
import uuid
from pathlib import Path

from . import desktop
from .core import UserError


class Programs:
    def __init__(self, folder):
        self.path = Path(folder) / "programs.json"

    def custom(self):
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise UserError("등록된 프로그램 목록을 읽지 못했어요.") from None
        if not isinstance(items, dict) or any(not isinstance(key, str) or not isinstance(item, dict) or
                not isinstance(item.get("name"), str) or not isinstance(item.get("path"), str) for key, item in items.items()):
            raise UserError("프로그램 목록의 형식이 올바르지 않습니다.")
        return items

    def catalog(self):
        return [{"id": key, "name": value[0]} for key, value in desktop.APPS.items()] + [
            {"id": key, "name": value["name"]} for key, value in self.custom().items()]

    def register(self, name, filename):
        path = Path(filename).resolve()
        name = name.strip()
        if not name or len(name) > 60:
            raise UserError("프로그램 이름은 1~60자로 입력하세요.")
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise UserError("실행할 .exe 파일을 직접 선택해 주세요.")
        items = self.custom()
        if len(items) >= 50:
            raise UserError("프로그램은 최대 50개까지 등록할 수 있어요.")
        identifier = uuid.uuid4().hex
        items[identifier] = {"name": name, "path": str(path)}
        self._save(items)
        return identifier

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"programs-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def remove(self, identifier):
        items = self.custom()
        if identifier not in items:
            raise UserError("직접 등록한 프로그램만 삭제할 수 있어요.")
        del items[identifier]
        self._save(items)

    def launch(self, identifier):
        if identifier in desktop.APPS:
            return desktop.launch(identifier)
        desktop.require_windows()
        item = self.custom().get(identifier)
        if item is None:
            raise UserError("등록되지 않은 프로그램입니다.")
        path = Path(item["path"])
        if not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file():
            raise UserError("등록한 실행 파일을 찾지 못했어요. 프로그램을 다시 등록해 주세요.")
        try:
            subprocess.Popen([str(path)], shell=False, cwd=str(path.parent))
        except OSError:
            raise UserError("프로그램을 실행하지 못했어요. 설치 경로를 확인해 주세요.") from None
        return f"{item['name']} 실행을 요청했어요."
