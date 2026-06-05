"""Domain models for Task Manager."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from tm_config import DEFAULT_STATE, FINISHED_STATES, PROGRESS_STATES, TESTING_STATES


TAG_PATTERN = re.compile(r"(?<!\w)#([A-Za-z0-9_-]+)")


def extract_tags_from_text(text: str) -> List[str]:
    """Return normalized hashtag tags found in a text."""
    seen = set()
    tags: List[str] = []
    for match in TAG_PATTERN.findall(text or ""):
        normalized = match.lower()
        if normalized not in seen:
            seen.add(normalized)
            tags.append(normalized)
    return tags


@dataclass
class Subtask:
    """Represents a subtask linked to a parent task."""

    title: str
    state: str = DEFAULT_STATE
    comments: List[str] = field(default_factory=list)
    task_id: Optional[str] = None
    source_line: Optional[int] = None
    due_date: Optional[datetime] = None
    priority: Optional[str] = None

    def is_finished(self) -> bool:
        """Check if subtask is in a finished state."""
        return self.state in FINISHED_STATES

    def is_in_progress(self) -> bool:
        """Check if subtask is in progress."""
        return self.state in PROGRESS_STATES

    def is_in_testing(self) -> bool:
        """Check if subtask is in testing."""
        return self.state in TESTING_STATES

    def get_tags(self) -> List[str]:
        """Return tags found in the subtask title."""
        return extract_tags_from_text(self.title)


@dataclass
class Task:
    """Represents a single task with its properties."""

    title: str
    state: str = DEFAULT_STATE
    comments: List[str] = field(default_factory=list)
    subtasks: List[Subtask] = field(default_factory=list)
    date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    priority: Optional[str] = None
    recurrence: Optional[str] = None
    time_spent: Optional[int] = None  # minutes
    blocked_by: List[str] = field(default_factory=list)  # task titles
    blocks: List[str] = field(default_factory=list)  # task titles
    task_id: Optional[str] = None
    source_line: Optional[int] = None

    def is_finished(self) -> bool:
        """Check if task is in a finished state."""
        return self.state in FINISHED_STATES

    def is_in_progress(self) -> bool:
        """Check if task is in progress."""
        return self.state in PROGRESS_STATES

    def is_in_testing(self) -> bool:
        """Check if task is in testing."""
        return self.state in TESTING_STATES

    def get_tags(self) -> List[str]:
        """Return tags found in the task title only (notes are plain text)."""
        return extract_tags_from_text(self.title)
