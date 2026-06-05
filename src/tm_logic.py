"""Business logic helpers for commands, IDs, and input normalization."""

import shlex
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Union

from .tm_config import DEFAULT_STATE, PRIORITY_ALIASES, RECURRENCE_ALIASES, STATE_ALIASES, VALID_PRIORITIES, VALID_RECURRENCES, VALID_STATES
from .tm_models import Subtask, Task


def normalize_state_input(state_input: str) -> Optional[str]:
    """Normalize user-provided state to canonical state name."""
    normalized = state_input.strip().upper().replace("_", " ")
    if normalized in VALID_STATES:
        return normalized
    if normalized in STATE_ALIASES:
        return STATE_ALIASES[normalized]
    return None


def normalize_priority_input(priority_input: str) -> Optional[str]:
    """Normalize user-provided priority to canonical priority name."""
    normalized = priority_input.strip().upper()
    if normalized in VALID_PRIORITIES:
        return normalized
    return PRIORITY_ALIASES.get(normalized)


def parse_date_input(date_input: str) -> Optional[datetime]:
    """Parse a date from dd/mm/yyyy or natural language (tomorrow, friday, +3d, etc.)."""
    raw = date_input.strip().lower()

    # Standard format
    try:
        return datetime.strptime(raw, "%d/%m/%Y")
    except ValueError:
        pass

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Relative: +Nd, +Nw, +Nm
    rel_match = re.match(r"^\+(\d+)([dwm])$", raw)
    if rel_match:
        n = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit == "d":
            return today + timedelta(days=n)
        elif unit == "w":
            return today + timedelta(weeks=n)
        elif unit == "m":
            month = today.month + n
            year = today.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = min(today.day, 28)
            return today.replace(year=year, month=month, day=day)

    # Keywords
    keywords = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "yesterday": today - timedelta(days=1),
        "next week": today + timedelta(weeks=1),
        "nextweek": today + timedelta(weeks=1),
    }
    if raw in keywords:
        return keywords[raw]

    # Day of week names
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day_abbrevs = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    target_day = None
    for i, (name, abbrev) in enumerate(zip(day_names, day_abbrevs)):
        if raw == name or raw == abbrev:
            target_day = i
            break
    if target_day is not None:
        current_day = today.weekday()
        days_ahead = (target_day - current_day) % 7
        if days_ahead == 0:
            days_ahead = 7  # next occurrence
        return today + timedelta(days=days_ahead)

    return None


def assign_task_ids(tasks_by_date: dict) -> None:
    """Assign session-only IDs based on current journal order."""
    next_id = 1

    for tasks in tasks_by_date.values():
        for task in tasks:
            task.task_id = str(next_id)
            next_id += 1

            for idx, subtask in enumerate(task.subtasks, start=1):
                subtask.task_id = f"{task.task_id}.{idx}"


def get_id_width(tasks_by_date: dict) -> int:
    """Return dynamic ID width based on total tasks currently loaded."""
    total_tasks = sum(len(tasks) for tasks in tasks_by_date.values())
    return max(1, len(str(total_tasks)))


def normalize_task_id_input(task_id_input: str) -> Optional[str]:
    """Normalize task ID input so 1, 01, 1.2, and 01.02 are equivalent."""
    match = re.match(r"^\s*0*(\d+)(?:\.0*(\d+))?\s*$", task_id_input)
    if not match:
        return None

    parent_id = str(int(match.group(1)))
    child_id = match.group(2)
    if child_id is None:
        return parent_id
    return f"{parent_id}.{int(child_id)}"


def find_task_by_id(tasks_by_date: dict, task_id: str) -> Optional[Union[Task, Subtask]]:
    """Find a task or subtask by assigned ID."""
    requested_id = normalize_task_id_input(task_id)
    if requested_id is None:
        return None

    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.task_id == requested_id:
                return task
            for subtask in task.subtasks:
                if subtask.task_id == requested_id:
                    return subtask
    return None


def normalize_note_id_input(note_id_input: str) -> Optional[Tuple[str, int]]:
    """Normalize note IDs like 1:n1 or 01:n02."""
    match = re.match(r"^\s*0*(\d+):n0*(\d+)\s*$", note_id_input, re.IGNORECASE)
    if not match:
        return None
    return str(int(match.group(1))), int(match.group(2))


def build_note_id(task_id: str, note_index: int) -> str:
    """Build a display ID for a task note."""
    return f"{task_id}:n{note_index}"


def find_note_by_id(tasks_by_date: dict, note_id: str) -> Optional[Tuple[Task, int, str]]:
    """Find a note by session ID, returning task, zero-based index, and text."""
    normalized = normalize_note_id_input(note_id)
    if normalized is None:
        return None

    requested_task_id, requested_note_index = normalized
    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.task_id == requested_task_id and 1 <= requested_note_index <= len(task.comments):
                note_index = requested_note_index - 1
                return task, note_index, task.comments[note_index]
    return None


def task_matches_search(task: Task, query: Optional[str]) -> bool:
    """Return whether a task matches a free-text or hashtag query."""
    if not query:
        return True

    normalized = query.strip().lower()
    if not normalized:
        return True

    if normalized.startswith("priority:"):
        expected = normalized.split(":", 1)[1].strip()
        if expected in ("", "any"):
            return task.priority is not None
        if expected in ("none", "null"):
            return task.priority is None
        return (task.priority or "").lower() == expected

    if normalized.startswith("due:"):
        expected = normalized.split(":", 1)[1].strip()
        today = datetime.now().date()
        due = task.due_date.date() if task.due_date else None
        if expected in ("", "any"):
            return due is not None
        if expected in ("none", "null"):
            return due is None
        if due is None:
            return False
        if expected == "overdue":
            return due < today
        if expected == "today":
            return due == today
        if expected in ("week", "thisweek"):
            return today <= due <= (today + timedelta(days=7))
        parsed_due = parse_date_input(expected)
        if parsed_due is not None:
            return due == parsed_due.date()
        return False

    haystacks = [task.title, *task.comments]
    haystacks.extend(subtask.title for subtask in task.subtasks)
    haystacks.extend(f"#{tag}" for tag in task.get_tags())
    haystacks.extend(f"#{tag}" for subtask in task.subtasks for tag in subtask.get_tags())

    return any(normalized in chunk.lower() for chunk in haystacks)


def normalize_recurrence_input(recurrence_input: str) -> Optional[str]:
    """Normalize user-provided recurrence to canonical value."""
    normalized = recurrence_input.strip().lower()
    if normalized in VALID_RECURRENCES:
        return normalized
    return RECURRENCE_ALIASES.get(normalized.upper())


def parse_new_command_args(
    raw_command: str,
) -> Tuple[Optional[str], Optional[str], Optional[datetime], Optional[datetime], Optional[str], Optional[str], Optional[str]]:
    """Parse new-task command arguments.

    Supported syntax:
    - n <title>
    - n <title> --state <state>
    - n <title> --date dd/mm/yyyy
    - n <title> --state <state> --date dd/mm/yyyy
    - n <title> --due dd/mm/yyyy --priority <low|medium|high|urgent>
    - n <title> --recur <daily|weekly|biweekly|monthly|yearly>
    """
    try:
        tokens = shlex.split(raw_command)
    except ValueError as exc:
        return None, None, None, None, None, None, f"Invalid command syntax: {exc}"

    if not tokens or tokens[0].lower() not in ("n", "new"):
        return None, None, None, None, None, None, "Usage: n [title] [--state <state>] [--date dd/mm/yyyy] [--due dd/mm/yyyy] [--priority <level>] [--recur <freq>]"

    args = tokens[1:]
    title_tokens: List[str] = []
    state_input: Optional[str] = None
    date_input: Optional[str] = None
    due_input: Optional[str] = None
    priority_input: Optional[str] = None
    recurrence_input: Optional[str] = None

    i = 0
    while i < len(args):
        token = args[i]
        token_lower = token.lower()

        if token_lower in ("--state", "-s"):
            i += 1
            state_tokens: List[str] = []
            while i < len(args) and args[i].lower() not in (
                "--state",
                "-s",
                "--date",
                "-d",
                "--due",
                "--priority",
                "-p",
                "--recur",
            ):
                state_tokens.append(args[i])
                i += 1
            if not state_tokens:
                return None, None, None, None, None, None, "Missing value for --state"
            state_input = " ".join(state_tokens)
            continue

        if token_lower in ("--date", "-d"):
            i += 1
            if i >= len(args):
                return None, None, None, None, None, None, "Missing value for --date"
            date_input = args[i]
            i += 1
            continue

        if token_lower == "--due":
            i += 1
            if i >= len(args):
                return None, None, None, None, None, None, "Missing value for --due"
            due_input = args[i]
            i += 1
            continue

        if token_lower in ("--priority", "-p"):
            i += 1
            if i >= len(args):
                return None, None, None, None, None, None, "Missing value for --priority"
            priority_input = args[i]
            i += 1
            continue

        if token_lower == "--recur":
            i += 1
            if i >= len(args):
                return None, None, None, None, None, None, "Missing value for --recur"
            recurrence_input = args[i]
            i += 1
            continue

        title_tokens.append(token)
        i += 1

    title = " ".join(title_tokens).strip()

    state = DEFAULT_STATE
    if state_input:
        normalized_state = normalize_state_input(state_input)
        if not normalized_state:
            return title, None, None, None, None, None, f"Invalid state: {state_input}"
        state = normalized_state

    target_date = None
    if date_input:
        target_date = parse_date_input(date_input)
        if target_date is None:
            return title, None, None, None, None, None, f"Invalid date: {date_input} (use dd/mm/yyyy)"

    due_date = None
    if due_input:
        due_date = parse_date_input(due_input)
        if due_date is None:
            return title, None, None, None, None, None, f"Invalid due date: {due_input} (use dd/mm/yyyy)"

    priority = None
    if priority_input:
        priority = normalize_priority_input(priority_input)
        if priority is None:
            return title, None, None, None, None, None, f"Invalid priority: {priority_input}"

    recurrence = None
    if recurrence_input:
        recurrence = normalize_recurrence_input(recurrence_input)
        if recurrence is None:
            return title, None, None, None, None, None, f"Invalid recurrence: {recurrence_input}. Valid: daily, weekly, biweekly, monthly, yearly"

    return title, state, target_date, due_date, priority, recurrence, None


def get_pending_tasks(tasks_by_date: dict) -> List[Task]:
    """Return all parent tasks that are not in finished states."""
    pending: List[Task] = []
    for tasks in tasks_by_date.values():
        pending.extend([task for task in tasks if not task.is_finished()])
    return pending


def build_pending_email_body(tasks_by_date: dict) -> str:
    """Build an email-ready text report for pending tasks and subtasks."""
    lines: List[str] = ["Pending tasks report", ""]

    sorted_dates = sorted([d for d in tasks_by_date.keys() if d is not None], reverse=True)
    if None in tasks_by_date:
        sorted_dates.append(None)

    has_content = False

    for date in sorted_dates:
        tasks = [task for task in tasks_by_date[date] if not task.is_finished()]
        if not tasks:
            continue

        has_content = True
        date_label = date.strftime("%A, %d/%m/%Y") if date else "No Date"
        lines.append(date_label)

        for task in tasks:
            task_id = task.task_id or "?"
            lines.append(f"- [{task_id}] {task.title} ({task.state})")

            for subtask in task.subtasks:
                if subtask.is_finished():
                    continue
                subtask_id = subtask.task_id or f"{task_id}.?"
                lines.append(f"  + [{subtask_id}] {subtask.title} ({subtask.state})")

            for comment in task.comments:
                lines.append(f"  : {comment}")

        lines.append("")

    if not has_content:
        lines.append("No pending tasks.")

    return "\n".join(lines).strip() + "\n"
