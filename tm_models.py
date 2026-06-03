"""Domain models for Task Manager."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from tm_config import DEFAULT_STATE, FINISHED_STATES


@dataclass
class Subtask:
    """Represents a subtask linked to a parent task."""

    title: str
    state: str = DEFAULT_STATE
    task_id: Optional[str] = None
    source_line: Optional[int] = None

    def is_finished(self) -> bool:
        """Check if subtask is in a finished state."""
        return self.state in FINISHED_STATES

    def is_in_progress(self) -> bool:
        """Check if subtask is in progress."""
        return self.state == "IN PROGRESS"

    def is_in_testing(self) -> bool:
        """Check if subtask is in testing."""
        return self.state in ("TESTING", "IN TESTING")


@dataclass
class Task:
    """Represents a single task with its properties."""

    title: str
    state: str = DEFAULT_STATE
    comments: List[str] = field(default_factory=list)
    subtasks: List[Subtask] = field(default_factory=list)
    date: Optional[datetime] = None
    task_id: Optional[str] = None
    source_line: Optional[int] = None

    def is_finished(self) -> bool:
        """Check if task is in a finished state."""
        return self.state in FINISHED_STATES

    def is_in_progress(self) -> bool:
        """Check if task is in progress."""
        return self.state == "IN PROGRESS"

    def is_in_testing(self) -> bool:
        """Check if task is in testing."""
        return self.state in ("TESTING", "IN TESTING")
