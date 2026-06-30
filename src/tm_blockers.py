"""Task dependencies / blockers — add, remove, resolve blockedBy and blocks metadata."""

import re
from typing import List, Optional

# Matches standalone hashtag tokens (#tag-name) for stripping in dependency lookups
_TAG_RE = re.compile(r"(?<!\S)#[\w-]+(?!\S)")


def extract_blockers_from_line(line: str) -> List[str]:
    """Extract blockedby: values (task titles) from a task line."""
    return re.findall(r"--\s*blockedby\s*[:=]\s*(.+?)(?:\s+--|$)", line, re.IGNORECASE)


def extract_blocks_from_line(line: str) -> List[str]:
    """Extract blocks: values (task titles) from a task line."""
    return re.findall(r"--\s*blocks\s*[:=]\s*(.+?)(?:\s+--|$)", line, re.IGNORECASE)


def add_blocker_metadata(line: str, blocker_title: str) -> str:
    """Add blockedby:Title to a task line."""
    return line.rstrip() + f" -- blockedby:{blocker_title.strip()}"


def add_blocks_metadata(line: str, blocked_title: str) -> str:
    """Add blocks:Title to a task line."""
    return line.rstrip() + f" -- blocks:{blocked_title.strip()}"


def remove_blocker_metadata(line: str, blocker_title: str) -> str:
    """Remove a specific blockedby:Title from a task line."""
    pattern = r"\s*--\s*blockedby\s*[:=]\s*" + re.escape(blocker_title.strip())
    return re.sub(pattern, "", line, count=1, flags=re.IGNORECASE).rstrip()


def remove_all_blocker_metadata(line: str) -> str:
    """Remove all blockedby: entries from a task line."""
    return re.sub(r"\s*--\s*blockedby\s*[:=]\s*.+?(?=\s+--|$)", "", line, flags=re.IGNORECASE).rstrip()


def remove_blocks_metadata(line: str, blocked_title: str) -> str:
    """Remove a specific blocks:Title from a task line."""
    pattern = r"\s*--\s*blocks\s*[:=]\s*" + re.escape(blocked_title.strip())
    return re.sub(pattern, "", line, count=1, flags=re.IGNORECASE).rstrip()


def remove_all_blocks_metadata(line: str) -> str:
    """Remove all blocks: entries from a task line."""
    return re.sub(r"\s*--\s*blocks\s*[:=]\s*.+?(?=\s+--|$)", "", line, flags=re.IGNORECASE).rstrip()


def find_task_by_title_match(tasks_by_date: dict, title: str) -> Optional["Task"]:
    """Find a task whose title matches (case-insensitive).

    Compares against both the full title and the tag-stripped title,
    since blockedby/blocks metadata stores tag-stripped titles.
    """
    from .tm_models import Task
    lower = title.strip().lower()
    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.title.strip().lower() == lower:
                return task
            stripped = " ".join(_TAG_RE.sub("", task.title).split()).lower()
            if stripped == lower:
                return task
    return None


def is_task_blocked(task: "Task", tasks_by_date: dict) -> bool:
    """Check if a task has unresolved blockers."""
    if not task.blocked_by:
        return False
    for blocker_title in task.blocked_by:
        blocker = find_task_by_title_match(tasks_by_date, blocker_title)
        if blocker and not blocker.is_finished():
            return True
    return False
