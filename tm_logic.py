"""Business logic helpers for commands, IDs, and input normalization."""

import shlex
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union

from tm_config import DEFAULT_STATE, STATE_ALIASES, VALID_STATES
from tm_models import Subtask, Task


def normalize_state_input(state_input: str) -> Optional[str]:
    """Normalize user-provided state to canonical state name."""
    normalized = state_input.strip().upper().replace("_", " ")
    if normalized in VALID_STATES:
        return normalized
    if normalized in STATE_ALIASES:
        return STATE_ALIASES[normalized]
    return None


def assign_task_ids(
    tasks_by_date: dict,
    id_registry: Dict[Tuple[str, str, Tuple[str, ...], int], str],
    next_id: int,
) -> int:
    """Assign stable per-run IDs to tasks."""
    seen_counts: Dict[Tuple[str, str, Tuple[str, ...]], int] = {}

    for date, tasks in tasks_by_date.items():
        date_key = date.strftime("%d/%m/%Y") if date else "NO_DATE"
        for task in tasks:
            base_key = (date_key, task.title, tuple(task.comments))
            occurrence = seen_counts.get(base_key, 0)
            seen_counts[base_key] = occurrence + 1

            task_key = (date_key, task.title, tuple(task.comments), occurrence)
            if task_key not in id_registry:
                id_registry[task_key] = str(next_id)
                next_id += 1

            task.task_id = id_registry[task_key]

            for idx, subtask in enumerate(task.subtasks, start=1):
                subtask.task_id = f"{task.task_id}.{idx}"

    return next_id


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


def parse_new_command_args(raw_command: str) -> Tuple[Optional[str], Optional[str], Optional[datetime], Optional[str]]:
    """Parse new-task command arguments.

    Supported syntax:
    - n <title>
    - n <title> --state <state>
    - n <title> --date dd/mm/yyyy
    - n <title> --state <state> --date dd/mm/yyyy
    """
    try:
        tokens = shlex.split(raw_command)
    except ValueError as exc:
        return None, None, None, f"Invalid command syntax: {exc}"

    if not tokens or tokens[0].lower() not in ("n", "new"):
        return None, None, None, "Usage: n [title] [--state <state>] [--date dd/mm/yyyy]"

    args = tokens[1:]
    title_tokens: List[str] = []
    state_input: Optional[str] = None
    date_input: Optional[str] = None

    i = 0
    while i < len(args):
        token = args[i]
        token_lower = token.lower()

        if token_lower in ("--state", "-s"):
            i += 1
            state_tokens: List[str] = []
            while i < len(args) and args[i].lower() not in ("--state", "-s", "--date", "-d"):
                state_tokens.append(args[i])
                i += 1
            if not state_tokens:
                return None, None, None, "Missing value for --state"
            state_input = " ".join(state_tokens)
            continue

        if token_lower in ("--date", "-d"):
            i += 1
            if i >= len(args):
                return None, None, None, "Missing value for --date"
            date_input = args[i]
            i += 1
            continue

        title_tokens.append(token)
        i += 1

    title = " ".join(title_tokens).strip()

    state = DEFAULT_STATE
    if state_input:
        normalized_state = normalize_state_input(state_input)
        if not normalized_state:
            return title, None, None, f"Invalid state: {state_input}"
        state = normalized_state

    target_date = None
    if date_input:
        try:
            target_date = datetime.strptime(date_input, "%d/%m/%Y")
        except ValueError:
            return title, None, None, f"Invalid date: {date_input} (use dd/mm/yyyy)"

    return title, state, target_date, None


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
