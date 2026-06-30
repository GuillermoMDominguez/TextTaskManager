"""Time tracking — parse, format, and update time-spent metadata."""

import re
from typing import List, Optional

# Time metadata stored as: -- spent:2h30m
_TIME_PATTERN = re.compile(r"(\d+)h(?:(\d+)m)?|(\d+)m")


def parse_time_spent(raw: str) -> Optional[int]:
    """Parse time string like '2h', '30m', '1h30m' into total minutes."""
    raw = raw.strip().lower()
    match = re.fullmatch(r"(\d+)h(\d+)m", raw)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = re.fullmatch(r"(\d+)h", raw)
    if match:
        return int(match.group(1)) * 60
    match = re.fullmatch(r"(\d+)m", raw)
    if match:
        return int(match.group(1))
    return None


def format_time_spent(minutes: int) -> str:
    """Format total minutes as XhYm string."""
    if minutes <= 0:
        return "0m"
    h = minutes // 60
    m = minutes % 60
    if h and m:
        return f"{h}h{m}m"
    elif h:
        return f"{h}h"
    return f"{m}m"


def extract_time_spent_from_line(line: str) -> Optional[int]:
    """Extract spent:XhYm from a task line, return minutes or None."""
    match = re.search(r"--\s*(?:spent|time)\s*[:=]\s*(\S+)", line, re.IGNORECASE)
    if match:
        return parse_time_spent(match.group(1))
    return None


def update_time_in_line(line: str, total_minutes: int) -> str:
    """Update or insert spent:XhYm metadata in a task line."""
    time_str = format_time_spent(total_minutes)
    new_line, count = re.subn(
        r"--\s*(?:spent|time)\s*[:=]\s*\S+",
        f"-- spent:{time_str}",
        line,
        count=1,
        flags=re.IGNORECASE,
    )
    if count:
        return new_line
    return line.rstrip() + f" -- spent:{time_str}"


def get_total_time_spent(tasks_by_date: dict) -> int:
    """Sum all time spent across all tasks in minutes."""
    total = 0
    for tasks in tasks_by_date.values():
        for task in tasks:
            if getattr(task, "time_spent", None):
                total += task.time_spent
    return total
