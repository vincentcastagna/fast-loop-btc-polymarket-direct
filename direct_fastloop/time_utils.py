from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


UTC = timezone.utc
QUESTION_RE = re.compile(
    r"^.+ - (?P<month>[A-Za-z]+) (?P<day>\d{1,2}), "
    r"(?P<start>\d{1,2}:\d{2}[AP]M)-(?P<end>\d{1,2}:\d{2}[AP]M) ET$"
)


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> int:
    month_cal = calendar.monthcalendar(year, month)
    hits = [week[weekday] for week in month_cal if week[weekday] != 0]
    return hits[n - 1]


def us_eastern_offset_hours(year: int, month: int, day: int) -> int:
    dst_start_day = nth_weekday_of_month(year, 3, calendar.SUNDAY, 2)
    dst_end_day = nth_weekday_of_month(year, 11, calendar.SUNDAY, 1)
    current = (month, day)
    if current < (3, dst_start_day):
        return -5
    if current > (11, dst_end_day):
        return -5
    if (3, dst_start_day) <= current < (11, dst_end_day):
        return -4
    return -5


def parse_iso_utc(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        cleaned = raw.replace("Z", "+00:00").replace(" ", "T")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def parse_fast_question_window(question: str, fallback_year: Optional[int] = None) -> Tuple[datetime, datetime]:
    fallback_year = fallback_year or datetime.now(UTC).year
    match = QUESTION_RE.match(question or "")
    if not match:
        raise ValueError(f"Unrecognized fast market question: {question!r}")

    month_name = match.group("month")
    month_num = datetime.strptime(month_name, "%B").month
    day = int(match.group("day"))
    start_str = match.group("start")
    end_str = match.group("end")
    et = timezone(timedelta(hours=us_eastern_offset_hours(fallback_year, month_num, day)))

    start_local = datetime.strptime(
        f"{month_name} {day} {fallback_year} {start_str}",
        "%B %d %Y %I:%M%p",
    ).replace(tzinfo=et)
    end_local = datetime.strptime(
        f"{month_name} {day} {fallback_year} {end_str}",
        "%B %d %Y %I:%M%p",
    ).replace(tzinfo=et)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def format_et(dt: datetime) -> str:
    return dt.astimezone(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d %H:%M:%S ET")

