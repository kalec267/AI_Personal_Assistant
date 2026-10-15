"""User-named sequences expanded into Luna's existing validated actions."""
import json
import re
import threading
import uuid
from pathlib import Path

from .core import UserError
from .programs import name_key

ALLOWED = {"launch_program", "set_volume", "open_website"}


class Routines:
    def __init__(self, folder, programs):
        self.path = Path(folder) / "routines.json"
        self.programs = programs
        self._lock = threading.RLock()

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                items = json.loads(source.read(1024 * 1024))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise UserError("루틴 목록을 읽지 못했어요. 설정 → 루틴을 확인하세요.") from None
        if not isinstance(items, dict) or len(items) > 50 or any(
                not isinstance(key, str) or not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not 1 <= len(item["name"].strip()) <= 60 or any(ord(char) < 32 for char in item["name"])
                or not isinstance(item.get("calls"), list) for key, item in items.items()):
            raise UserError("루틴 저장 형식이 올바르지 않습니다.")
        return items

    def catalog(self):
        return [{"id": key, "name": item["name"], "count": len(item["calls"])} for key, item in self._read().items()]

    def get(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise UserError("루틴 이름을 1~60자로 지정하세요.")
        matches = [item for item in self._read().values() if name_key(item["name"]) == name_key(name)]
        if len(matches) != 1:
            raise UserError("등록된 루틴 이름을 하나로 지정하세요. 설정 → 루틴에서 먼저 등록할 수 있어요.")
        calls = matches[0]["calls"]
        if not 1 <= len(calls) <= 5 or any(not isinstance(call, dict) or not isinstance(call.get("function"), dict)
                                         or call["function"].get("name") not in ALLOWED for call in calls):
            raise UserError("루틴에는 등록 프로그램·볼륨·HTTPS 사이트 동작을 1~5개 저장할 수 있어요.")
        return calls

    def resolve(self, text):
        key = name_key(text.strip().rstrip(".!?。！？"))
        matches = [item for item in self.catalog() if re.fullmatch(re.escape(name_key(item["name"])) +
                   r"(?:루틴)?(?:을|를)?(?:(?:실행|시작)(?:해)?|켜)?(?:줘|주세요|해줘|해주세요)?", key)]
        if len(matches) > 1:
            raise UserError("루틴 이름이 겹칩니다. 설정에서 서로 구분할 수 있는 이름으로 바꾸세요.")
        return matches[0] if matches else None

    def save(self, name, program_ids, volume=None, url="", identifier=None):
        from .actions import validate_calls
        name = name.strip() if isinstance(name, str) else ""
        if not 1 <= len(name) <= 60 or any(ord(char) < 32 for char in name):
            raise UserError("루틴 이름은 1~60자로 입력하세요.")
        if any(name_key(item["name"]) == name_key(name) for item in self.programs.catalog()):
            raise UserError("프로그램 이름과 구분할 수 있는 루틴 이름을 사용하세요.")
        if not isinstance(program_ids, (list, tuple)) or any(not isinstance(item, str) for item in program_ids) or len(set(program_ids)) != len(program_ids):
            raise UserError("실행할 프로그램을 중복 없이 선택하세요.")
        def call(tool, args):
            return {"function": {"name": tool, "arguments": args}}
        calls = [call("launch_program", {"app_id": item}) for item in program_ids]
        if volume is not None:
            calls.append(call("set_volume", {"mode": "set", "value": volume}))
        if url:
            calls.append(call("open_website", {"url": url}))
        validate_calls(calls, self.programs)
        with self._lock:
            items = self._read()
            if identifier is not None and identifier not in items:
                raise UserError("수정할 루틴이 없어졌어요. 목록을 새로 확인하세요.")
            if any(key != identifier and name_key(item["name"]) == name_key(name) for key, item in items.items()):
                raise UserError("이미 등록된 루틴 이름입니다.")
            if identifier is None and len(items) >= 50:
                raise UserError("루틴은 최대 50개까지 등록할 수 있어요.")
            identifier = identifier or uuid.uuid4().hex
            items[identifier] = {"name": name, "calls": calls}
            self._save(items)
        return identifier

    def remove(self, identifier):
        with self._lock:
            items = self._read()
            if identifier not in items:
                raise UserError("삭제할 루틴이 없습니다.")
            del items[identifier]
            self._save(items)

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"routines-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
