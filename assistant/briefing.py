"""Briefings use saved calendar facts; no network or desktop actions."""
import datetime as dt
import math


def short_title(event):
    title = " ".join(event["title"].split())
    return title[:117] + "…" if len(title) > 120 else title


def daily_briefing(events, now=None):
    now = now or dt.datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=1)
    parsed = sorted([(dt.datetime.fromisoformat(item["start"]), dt.datetime.fromisoformat(item["end"]), item)
                     for item in events], key=lambda item: (item[0], item[2]["id"]))
    today = [item for item in parsed if item[0] < end and item[1] > start]
    ongoing = [item for item in today if item[0] <= now < item[1]]
    upcoming = [item for item in today if item[0] > now]
    weekdays = "월화수목금토일"
    lines = [f"오늘은 {now.month}월 {now.day}일 {weekdays[now.weekday()]}요일, 현재 {now:%H:%M}입니다."]
    if not today:
        lines.append("오늘 등록된 일정은 없습니다.")
    else:
        completed = len(today) - len(ongoing) - len(upcoming)
        lines.append(f"오늘 일정은 {len(today)}건입니다. 완료 시각이 지난 일정 {completed}건, 진행 중 {len(ongoing)}건, 남은 일정 {len(upcoming)}건입니다.")
        for begin, finish, event in (ongoing + upcoming)[:3]:
            state = "진행 중" if begin <= now else f"{begin:%H:%M} 시작"
            finish_label = finish.strftime("%H:%M" if finish.date() == now.date() else "%m월 %d일 %H:%M")
            lines.append(f"{state}: {short_title(event)} · {finish_label} 종료")
        if len(ongoing) + len(upcoming) > 3:
            lines.append(f"그 외 남은 일정 {len(ongoing) + len(upcoming) - 3}건은 일정 목록에서 확인할 수 있습니다.")
        latest_end = None
        for begin, finish, _event in ongoing + upcoming:
            if latest_end is not None and begin < latest_end:
                lines.append("남은 일정 중 시간이 겹치는 일정이 있습니다.")
                break
            latest_end = max(latest_end, finish) if latest_end is not None else finish
    if not ongoing and not upcoming:
        future = next((item for item in parsed if item[0] >= end), None)
        if future:
            lines.append(f"다음 일정은 {future[0]:%m월 %d일 %H:%M}, {short_title(future[2])}입니다.")
    return "\n".join(lines)


def reminder_message(events, now):
    lines = []
    for event in events:
        begin = dt.datetime.fromisoformat(event["start"])
        minutes = max(0, math.ceil((begin - now).total_seconds() / 60))
        when = f"약 {minutes}분 뒤 시작합니다" if minutes else "지금 시작합니다"
        lines.append(f"일정 알림: {short_title(event)} · {begin:%H:%M} · {when}.")
    return "\n".join(lines)
