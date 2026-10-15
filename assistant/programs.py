"""Only user-registered executables can be launched by the model."""
import json
import functools
import re
import subprocess
import unicodedata
import threading
import uuid
from pathlib import Path

from . import desktop
from .core import UserError

LAUNCH_VERB = r"(?:열어(?:\s*(?:줘|주세요))?|켜(?:\s*(?:줘|주세요))?|(?:실행|시작)(?:\s*해(?:\s*(?:줘|주세요))?)?)"


def name_key(name):
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


def mutation(method):
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class Programs:
    def __init__(self, folder):
        self.path = Path(folder) / "programs.json"
        self._lock = threading.RLock()

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
        return [{"id": key, "name": value["name"]} for key, value in self.custom().items()]

    def resolve(self, text):
        """Match a whole launch request; never execute incidental app mentions."""
        request = name_key(text.strip().rstrip(".!?。！？"))
        items = self.catalog()
        matches = []
        for item in items:
            key = name_key(item["name"])
            if key and re.fullmatch(re.escape(key) + r"(?:을|를)?(?:좀)?" + LAUNCH_VERB, request):
                matches.append(item)
        if len(matches) > 1:
            raise UserError("같은 명령에 해당하는 등록 이름이 여러 개예요. 설정 → 프로그램에서 서로 다른 이름으로 다시 등록해 주세요.")
        return matches[0] if matches else None

    @staticmethod
    def launch_target(text):
        match = re.fullmatch(r"\s*(.+?)\s*(?:좀\s*)?" + LAUNCH_VERB + r"\s*[.!?。！？]?\s*", text)
        if not match:
            return None
        target = match[1].strip()
        # Compound requests, questions and negations remain whole requests for AI.
        if re.search(r"(?:하고|켜고|열고|그리고|말고|하지|취소|방법|어떻게|사용법|예시|\?)", target):
            return None
        return target

    @mutation
    def register(self, name, filename):
        path = Path(filename).resolve()
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 60 or any(ord(char) < 32 for char in name):
            raise UserError("프로그램 이름은 1~60자로 입력하세요.")
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise UserError("실행할 .exe 파일을 직접 선택해 주세요.")
        items = self.custom()
        if any(name_key(item["name"]) == name_key(name) for item in items.values()):
            raise UserError("이미 등록된 이름이에요. 음성으로 구분할 수 있는 다른 이름을 입력해 주세요.")
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

    @mutation
    def remove(self, identifier, *, expected=None):
        items = self.custom()
        if identifier not in items:
            raise UserError("직접 등록한 프로그램만 삭제할 수 있어요.")
        if expected is not None and {"id": identifier, **items[identifier]} != expected:
            raise UserError("프로그램 등록 정보가 바뀌었어요. 다시 요청하세요.")
        del items[identifier]
        self._save(items)

    @mutation
    def rename(self, identifier, name, *, expected=None):
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 60 or any(ord(char) < 32 for char in name):
            raise UserError("프로그램 이름은 1~60자로 입력하세요.")
        items = self.custom()
        if identifier not in items or expected is not None and {"id": identifier, **items[identifier]} != expected:
            raise UserError("프로그램 등록 정보가 바뀌었어요. 다시 요청하세요.")
        if any(key != identifier and name_key(item["name"]) == name_key(name) for key, item in items.items()):
            raise UserError("이미 등록된 이름입니다.")
        items[identifier]["name"] = name
        self._save(items)

    def launch(self, identifier):
        item = self.custom().get(identifier)
        if item is None:
            raise UserError("등록되지 않은 프로그램입니다.")
        desktop.require_windows()
        path = Path(item["path"])
        if not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file():
            raise UserError("등록한 실행 파일을 찾지 못했어요. 프로그램을 다시 등록해 주세요.")
        try:
            subprocess.Popen([str(path)], shell=False, cwd=str(path.parent))
        except OSError:
            raise UserError("프로그램을 실행하지 못했어요. 설치 경로를 확인해 주세요.") from None
        return f"{item['name']} 실행을 요청했어요."
