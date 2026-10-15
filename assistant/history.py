"""Bounded conversational text; no executable tool calls are stored or replayed."""
import json
import threading
import uuid
from pathlib import Path

MAX_CHARS = 20000


class ConversationMemory:
    def __init__(self, folder):
        self.path = Path(folder) / "conversation.json"
        self._lock = threading.RLock()
        self.scope = None
        self.generation = 0
        self.turns = []
        self.remember = False
        self.limit = 10
        self.warning = ""

    def configure(self, scope, remember=False, limit=10):
        with self._lock:
            initial = self.scope is None
            changed = self.scope != tuple(scope)
            if changed:
                self.scope = tuple(scope)
                self.turns = []
                self.generation += 1
                if initial and remember:
                    self._load()
            self.remember = bool(remember)
            self.limit = max(1, min(20, int(limit)))
            self._trim()
            self._save()
            self.warning = ""
            return changed and not initial

    def _load(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                value = json.loads(source.read(200001))
            if not isinstance(value, dict) or value.get("scope") != list(self.scope) or not isinstance(value.get("turns"), list):
                return
            for item in value["turns"][-20:]:
                if isinstance(item, dict) and all(isinstance(item.get(key), str) for key in ("user", "assistant")):
                    self.turns.append({"id": uuid.uuid4().hex, "user": item["user"][:500], "assistant": item["assistant"][:6000]})
        except (OSError, ValueError):
            return

    def _trim(self):
        self.turns = self.turns[-self.limit:]
        while len(self.turns) > 1 and sum(len(t["user"]) + len(t["assistant"]) for t in self.turns) > MAX_CHARS:
            self.turns.pop(0)

    def _save(self):
        if not self.remember:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"conversation-{uuid.uuid4().hex}.tmp")
        try:
            data = {"scope": list(self.scope), "turns": [{"user": t["user"], "assistant": t["assistant"]} for t in self.turns]}
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def snapshot(self):
        with self._lock:
            return self.generation, [dict(item) for item in self.turns]

    def messages(self):
        _, turns = self.snapshot()
        return [message for turn in turns for message in ({"role": "user", "content": turn["user"]}, {"role": "assistant", "content": turn["assistant"]})]

    def append(self, user, assistant, generation):
        with self._lock:
            if generation != self.generation:
                return None
            identifier = uuid.uuid4().hex
            self.turns.append({"id": identifier, "user": user[:500], "assistant": assistant[:6000]})
            self._trim()
            self._save_turn()
            return identifier

    def update(self, identifier, assistant):
        with self._lock:
            for turn in self.turns:
                if turn["id"] == identifier:
                    turn["assistant"] = assistant[:6000]
                    self._trim()
                    self._save_turn()
                    break

    def _save_turn(self):
        try:
            self._save()
            self.warning = ""
        except OSError:
            # An already executed desktop operation must not become an apparent
            # execution failure just because conversation persistence failed.
            self.warning = "최근 대화를 파일에 저장하지 못했어요. 현재 세션에서는 기억합니다."

    def clear(self):
        with self._lock:
            self.turns = []
            self.generation += 1
            self._save()
            self.warning = ""
