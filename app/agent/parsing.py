"""Lenient parsers for values produced by a small local LLM."""

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

try:
    import jdatetime
    _HAS_JDATETIME = True
except ImportError:
    _HAS_JDATETIME = False

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


# Persian month names (Jalali)
_PERSIAN_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
    # Without ی
    "فروردين": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}

# Gregorian month names
_GREGORIAN_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _persian_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert Jalali (Persian) date to Gregorian date using jdatetime."""
    if _HAS_JDATETIME:
        return jdatetime.date(year, month, day).togregorian()
    # Fallback: minimal implementation for when jdatetime is not available
    # (kept for compatibility)
    raise ValueError("jdatetime library required for Persian calendar conversion")


def _gregorian_to_persian(d: date) -> tuple[int, int, int]:
    """Convert Gregorian date to Jalali (Persian) using jdatetime."""
    if _HAS_JDATETIME:
        jd = jdatetime.date.fromgregorian(date=d)
        return jd.year, jd.month, jd.day
    # Fallback
    raise ValueError("jdatetime library required for Persian calendar conversion")


def parse_date(value: str, user_timezone: str = "Asia/Tehran") -> date | None:
    """Parse a date string. Accepts:
    - '16 آذر', '۱۶ آذر' (current Jalali year)
    - '16 آذر ۱۴۰۳', '۱۶ آذر ۱۴۰۳'
    - '2025-03-15', '15/03/2025'
    - 'in 2 weeks', '2 weeks later'
    - 'دو هفته دیگه', '2 هفته بعد'
    - 'فردا', 'امروز'
    Returns Gregorian date or None if not a date."""
    if not value or value in ("", "null", "none"):
        return None

    s = str(value).translate(_DIGITS).strip().lower()
    now = datetime.now(ZoneInfo(user_timezone)).date()

    # Handle relative dates
    if s in ("today", "امروز"):
        return now
    if s in ("tomorrow", "فردا"):
        from datetime import timedelta
        return now + timedelta(days=1)

    # Relative: "in X days/weeks/months" / "X روز/هفته/ماه دیگه/بعد"
    rel_match = re.search(r"(?:in\s+|)(\d+)\s*(day|days|week|weeks|month|months|روز|هفته|ماه)\s*(?:later|after|دیگه|بعد|دیگر)?", s)
    if rel_match:
        from datetime import timedelta
        num = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith("day") or unit == "روز":
            return now + timedelta(days=num)
        elif unit.startswith("week") or unit == "هفته":
            return now + timedelta(weeks=num)
        elif unit.startswith("month") or unit == "ماه":
            # Approximate month as 30 days
            return now + timedelta(days=num * 30)

    # Handle "X days/weeks later" patterns
    rel_match2 = re.search(r"(\d+)\s*(?:روز|هفته|ماه)\s*(?:بعد|دیگه|دیگر|later)", s)
    if rel_match2:
        from datetime import timedelta
        num = int(rel_match2.group(1))
        # Need to figure out unit from original string
        if "روز" in s:
            return now + timedelta(days=num)
        elif "هفته" in s:
            return now + timedelta(weeks=num)
        elif "ماه" in s:
            return now + timedelta(days=num * 30)

    # Parse "16 آذر" or "16 آذر 1403"
    # Persian: "۱۶ آذر" or "۱۶ آذر ۱۴۰۳"
    parts = s.replace("،", " ").replace(",", " ").split()
    if len(parts) >= 2:
        # Check if second part is a Persian month
        day_str = parts[0]
        month_str = parts[1]
        year_str = parts[2] if len(parts) > 2 else None

        if day_str.isdigit() and month_str in _PERSIAN_MONTHS:
            day = int(day_str)
            month = _PERSIAN_MONTHS[month_str]
            # Default to current Jalali year
            if year_str and year_str.isdigit():
                year = int(year_str)
            else:
                # Get current Jalali year
                jy, _, _ = _gregorian_to_persian(now)
                year = jy
            try:
                return _persian_to_gregorian(year, month, day)
            except ValueError:
                pass

    # Parse Gregorian: "2025-03-15", "15/03/2025", "03/15/2025"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    return None


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
    if joined in ("every", "everyday", "every day", "daily", "all", "all days", "any"):
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
