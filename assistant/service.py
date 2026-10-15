"""Native application operations, independent of UI and HTTP."""
import datetime as dt
from pathlib import Path

from . import conversation, desktop
from .core import Calendar, parse_command


class AssistantService:
    def __init__(self, data_dir):
        self.calendar = Calendar(Path(data_dir) / "calendar.sqlite3")

    def events_on(self, day):
        day = dt.date.fromisoformat(day)
        start = day.isoformat() + "T00:00"
        end = (day + dt.timedelta(days=1)).isoformat() + "T00:00"
        return [event for event in self.calendar.list() if event["start"] < end and event["end"] > start]

    def execute(self, text):
        intent = parse_command(text)
        action = intent["action"]
        if action == "create":
            return {"message": "일정 초안을 만들었어요. 날짜와 시간을 확인하고 저장해 주세요. 기본 길이는 1시간이에요.", "draft": intent}
        if action == "list":
            events = self.events_on(intent["day"]) if intent["day"] else self.calendar.list()
            message = "저장된 일정이 없어요." if not events else f"일정 {len(events)}개가 있어요. " + " / ".join(
                f"{event['start'].replace('T', ' ')} {event['title']}" for event in events[:10])
        elif action == "launch":
            message = desktop.launch(intent["app"])
        elif action == "volume":
            message = desktop.volume(intent["mode"], intent.get("value", 0))
        else:
            message = conversation.answer(text)
        return {"message": message}
