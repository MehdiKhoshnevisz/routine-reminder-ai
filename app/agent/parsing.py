"""Lenient parsers for values produced by a small local LLM."""

import re
from datetime import time

_DAY_ALIASES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
    # Persian
    "دوشنبه": 0, "سه‌شنبه": 1, "سهشنبه": 1, "چهارشنبه": 2,
    "پنجشنبه": 3, "پنج‌شنبه": 3, "جمعه": 4, "شنبه": 5, "یکشنبه": 6, "یک‌شنبه": 6,
}

# Persian / Arabic-Indic digits -> ASCII, so "۷:۳۰" parses like "7:30".
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def parse_time(value) -> time:
    """Accepts '07:10', '7:10', '19', '7pm', '7:30 PM', '1900'."""
    s = str(value).translate(_DIGITS).strip().lower().replace(".", ":").replace("٫", ":")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*(am|pm)?", s)
    if not m:
        raise ValueError(f"ساعت «{value}» رو متوجه نشدم. به شکل HH:MM (۲۴ ساعته) بنویس.")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"ساعت «{value}» نامعتبره.")
    return time(hour, minute)


_FILLER = {"on", "and", "every", "each", "the", "at", "in", "&", "only", "و", "هر", "ها", "روز", "روزهای"}


def _day(word: str) -> int | None:
    """'saturday', 'Saturdays', 'sat', 'sat.' -> 5"""
    w = word.strip().lower().rstrip(".")
    if w in _DAY_ALIASES:
        return _DAY_ALIASES[w]
    if w.endswith("s") and w[:-1] in _DAY_ALIASES:
        return _DAY_ALIASES[w[:-1]]
    return None


def parse_days(value) -> list[int]:
    """Accepts [0,2], ['monday','fri'], 'saturdays', 'every saturday', 'mon-fri', 'weekdays',
    'weekends', 'every day', '' -> [] (= every day).
    'every' -> explicit recurring daily (returns []).
    Specific day names like 'monday' -> [0]."""
    if value in (None, "", [], "null"):
        return []  # Default to every day (for backward compat), but actions.py overrides to single day
    items = value if isinstance(value, list) else [value]
    joined = " ".join(str(i).lower() for i in items).replace(",", " ").replace("،", " ").strip()
    # Join multi-word Persian day names ("سه شنبه" -> "سه‌شنبه") so they split as one word.
    joined = re.sub(r"(سه|پنج|یک)\s+شنبه", "\\1\u200cشنبه", joined).replace("\u200cها", "")

    # Handle explicit "every" for recurring daily
    if joined in ("every", "everyday", "daily", "all", "all days", "any"):
        return []
    if "weekday" in joined:
        return [0, 1, 2, 3, 4]
    if "weekend" in joined:
        return [5, 6]
    rng = re.fullmatch(r"\s*([a-z]+)\s*(?:-|to|until|through|till)\s*([a-z]+)\s*", joined)
    if rng and _day(rng.group(1)) is not None and _day(rng.group(2)) is not None:
        a, b = _day(rng.group(1)), _day(rng.group(2))
        return sorted({(a + i) % 7 for i in range((b - a) % 7 + 1)})
    days: set[int] = set()
    for word in joined.split():
        if word in _FILLER:
            continue
        if word.isdigit() and 0 <= int(word) <= 6:
            days.add(int(word))
        elif (d := _day(word)) is not None:
            days.add(d)
        else:
            raise ValueError(f"روز «{word}» ناشناخته‌ست.")
    return sorted(days)
