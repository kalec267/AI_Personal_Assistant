from __future__ import annotations

import datetime as dt
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class UserError(ValueError):
    pass


class Calendar:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY start")]

    def add(self, title, start, end):
        title = str(title).strip()
        if not title or len(title) > 200:
            raise UserError("일정 제목은 1~200자로 입력해 주세요.")
        try:
            begin, finish = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
        except (ValueError, TypeError):
            raise UserError("올바른 시작·종료 시간을 입력해 주세요.") from None
        if begin.tzinfo or finish.tzinfo:
            raise UserError("시간대 없는 PC 현지 시간을 입력해 주세요.")
        if finish <= begin:
            raise UserError("종료 시간은 시작 시간 이후여야 합니다.")
        event = dict(id=uuid.uuid4().hex, title=title, start=begin.isoformat(timespec="minutes"), end=finish.isoformat(timespec="minutes"))
        with self.connect() as db:
            db.execute("INSERT INTO events VALUES (:id, :title, :start, :end)", event)
        return event

    def delete(self, event_id):
        with self.connect() as db:
            if not db.execute("DELETE FROM events WHERE id=?", (event_id,)).rowcount:
                raise UserError("일정을 찾을 수 없습니다.")

    def export(self):
        def escape(value):
            return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

        # Fold by UTF-8 bytes, as required by RFC 5545.
        def fold(line):
            result, chunk = [], ""
            for char in line:
                if len((chunk + char).encode("utf-8")) > 75:
                    result.append(chunk)
                    chunk = " "
                chunk += char
            return "\r\n".join(result + [chunk])

        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Luna Assistant//KO", "CALSCALE:GREGORIAN"]
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for event in self.list():
            lines += ["BEGIN:VEVENT", f"UID:{event['id']}@luna.local", f"DTSTAMP:{stamp}",
                      "DTSTART:" + dt.datetime.fromisoformat(event["start"]).strftime("%Y%m%dT%H%M%S"),
                      "DTEND:" + dt.datetime.fromisoformat(event["end"]).strftime("%Y%m%dT%H%M%S"),
                      "SUMMARY:" + escape(event["title"]), "END:VEVENT"]
        return "\r\n".join(map(fold, lines + ["END:VCALENDAR"])) + "\r\n"


def parse_command(text: str, now: dt.datetime | None = None):
    """Parse supported Korean commands; never evaluate speech as code."""
    now = now or dt.datetime.now()
    text = text.strip()
    if not text or len(text) > 500:
        raise UserError("명령을 1~500자로 입력해 주세요.")
    if re.search(r"하지\s*마|하지\s*말|말고|취소해|실행\s*취소", text):
        raise UserError("명령을 실행하지 않았어요. 원하는 동작만 한 가지씩 말씀해 주세요.")
    if re.search(r"방법|어떻게|사용법|예시", text):
        return dict(action="help")
    schedule = re.search(r"일정|스케줄|캘린더", text)
    calendar_request = schedule and re.search(r"추가|등록|만들|잡아|작성|보여|알려|목록|확인", text)
    if not calendar_request and re.search(r"(음소거\s*(해제|풀어)|소리\s*켜)", text):
        return dict(action="volume", mode="unmute")
    if not calendar_request and ("음소거" in text or "소리 꺼" in text):
        return dict(action="volume", mode="mute")
    if not calendar_request and re.search(r"볼륨|소리|음량", text):
        number = re.search(r"(\d+)\s*(?:%|퍼센트)", text) or re.search(r"(?:볼륨|소리|음량)(?:을|를)?\s*(\d+)", text)
        if number:
            value = int(number[1])
            if not 0 <= value <= 100:
                raise UserError("볼륨은 0~100 사이로 지정해 주세요.")
            return dict(action="volume", mode="set", value=value)
        if re.search(r"올려|높여|크게", text):
            return dict(action="volume", mode="up")
        if re.search(r"내려|낮춰|작게|줄여", text):
            return dict(action="volume", mode="down")
        raise UserError("예: 볼륨 30퍼센트, 볼륨 올려, 음소거 해제")
    if schedule and re.search(r"보여|알려|목록|확인", text):
        day = None
        for token, offset in [("모레", 2), ("내일", 1), ("오늘", 0)]:
            if token in text:
                day = (now.date() + dt.timedelta(days=offset)).isoformat()
                break
        return dict(action="list", day=day)
    if schedule and re.search(r"추가|등록|만들|잡아|작성", text):
        day = None
        day_pattern = r"오늘|내일|모레|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}월\s*\d{1,2}일"
        match = re.search(day_pattern, text)
        if not match:
            raise UserError("날짜를 알려 주세요. 예: 내일 오후 3시 팀 회의 일정 추가")
        token = match[0]
        try:
            if token in ("오늘", "내일", "모레"):
                day = now.date() + dt.timedelta(days={"오늘": 0, "내일": 1, "모레": 2}[token])
            elif "월" in token:
                month, date = map(int, re.findall(r"\d+", token))
                day = dt.date(now.year, month, date)
            else:
                year, month, date = map(int, token.split("-"))
                day = dt.date(year, month, date)
        except ValueError:
            raise UserError("존재하지 않는 날짜입니다.") from None
        time_pattern = r"(?:(오전|오후)\s*)?(\d{1,2})(?:시(?:\s*(반|\d{1,2}\s*분))?|:(\d{2}))(?:에)?"
        time_match = re.search(time_pattern, text)
        if not time_match:
            raise UserError("시간을 알려 주세요. 예: 내일 오후 3시 팀 회의 일정 추가")
        period, hour, minute_word, minute_colon = time_match.groups()
        hour = int(hour)
        minute = 30 if minute_word == "반" else int(re.sub(r"\D", "", minute_word or minute_colon or "0"))
        if period:
            if not 1 <= hour <= 12:
                raise UserError("오전·오후 시간은 1~12시로 입력해 주세요.")
            hour = hour % 12 + (12 if period == "오후" else 0)
        elif 1 <= hour <= 12:
            raise UserError("오전인지 오후인지 알려 주세요. 또는 15:00처럼 입력하세요.")
        try:
            begin = dt.datetime.combine(day, dt.time(hour, minute))
        except ValueError:
            raise UserError("존재하지 않는 시간입니다.") from None
        # Remove only the matched date/time and command words, retaining the event title.
        title = text
        for start, end in sorted([match.span(), time_match.span()], reverse=True):
            title = title[:start] + " " + title[end:]
        title = re.sub(r"(?:일정|스케줄|캘린더)(?:을|를)?|추가(?:해\s*줘|해줘|해)?|등록(?:해\s*줘|해줘|해)?|만들어\s*줘|작성해\s*줘|잡아\s*줘", " ", title)
        title = " ".join(title.split()).strip(" .,!?")
        if not title:
            raise UserError("일정 제목도 알려 주세요. 예: 내일 오후 3시 팀 회의 일정 추가")
        return dict(action="create", title=title, start=begin.isoformat(timespec="minutes"), end=(begin + dt.timedelta(hours=1)).isoformat(timespec="minutes"))
    return dict(action="help")
