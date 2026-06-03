"""Journal parsing and persistence helpers."""

import re
import sys
from datetime import datetime
from typing import List, Optional

from tm_config import DEFAULT_STATE, STATE_ALIASES, VALID_STATES
from tm_models import Task


def split_comments(text: str) -> List[str]:
    """Split a text by ':' into separate comments, filtering empty ones."""
    return [c.strip() for c in text.split(":") if c.strip()]


def parse_task_line(line: str) -> Optional[Task]:
    """Parse a single task line and extract title, state, and comments."""
    stripped = line.strip()
    if not stripped.startswith("-"):
        return None

    content = stripped[1:].strip()
    if not content:
        return None

    task = Task(title="")
    comments: List[str] = []
    current_state = DEFAULT_STATE

    parts = re.split(r"\s*(?:--|->)\s*", content)
    title_part = parts[0]

    if ":" in title_part:
        idx = title_part.find(":")
        task.title = title_part[:idx].strip()
        comments.extend(split_comments(title_part[idx + 1 :]))
    else:
        task.title = title_part.strip()

    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue

        state_found = None
        remaining = part

        for alias, canonical in STATE_ALIASES.items():
            if part.upper().startswith(alias):
                state_found = canonical
                remaining = part[len(alias) :].strip()
                break

        if not state_found:
            for state in VALID_STATES:
                if part.upper().startswith(state):
                    state_found = state
                    remaining = part[len(state) :].strip()
                    break

        if state_found:
            current_state = state_found
            remaining = remaining.lstrip()
            if remaining.startswith(":"):
                comments.extend(split_comments(remaining[1:]))
            elif remaining:
                comments.extend(split_comments(remaining))
        else:
            comments.extend(split_comments(part))

    task.state = current_state
    task.comments = comments
    return task


def parse_date(line: str) -> Optional[datetime]:
    """Parse a date line in format '## dd/mm/yyyy'."""
    match = re.match(r"^##\s*(\d{1,2}/\d{1,2}/\d{4})\s*$", line.strip())
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y")
        except ValueError:
            return None
    return None


def parse_journal(filepath: str) -> dict:
    """Parse the journal file and extract all tasks grouped by date."""
    tasks_by_date = {}
    current_date = None
    last_task: Optional[Task] = None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                date = parse_date(line)
                if date:
                    current_date = date
                    last_task = None
                    if current_date not in tasks_by_date:
                        tasks_by_date[current_date] = []
                    continue

                stripped = line.strip()
                if stripped.startswith(":"):
                    if last_task is not None:
                        last_task.comments.extend(split_comments(stripped[1:]))
                    continue

                if stripped.startswith("-") and not stripped.startswith("--"):
                    task = parse_task_line(line)
                    if task and task.title:
                        task.date = current_date
                        task.source_line = line_number
                        if current_date:
                            tasks_by_date[current_date].append(task)
                        else:
                            if None not in tasks_by_date:
                                tasks_by_date[None] = []
                            tasks_by_date[None].append(task)
                        last_task = task

    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        sys.exit(1)
    except Exception as exc:
        print(f"Error reading file: {exc}")
        sys.exit(1)

    return tasks_by_date


def update_task_state_in_file(filepath: str, task: Task, new_state: str) -> bool:
    """Persist a task state change in the journal file."""
    if task.source_line is None:
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        original_line = lines[line_index]
        indent_match = re.match(r"^(\s*)-\s*", original_line)
        indent = indent_match.group(1) if indent_match else ""

        new_line = f"{indent}- {task.title} -- {new_state}"
        if task.comments:
            new_line += " : " + " : ".join(task.comments)
        new_line += "\n"

        lines[line_index] = new_line

        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return True
    except Exception:
        return False


def add_task_to_file(filepath: str, title: str, state: str = DEFAULT_STATE, target_date: Optional[datetime] = None) -> bool:
    """Append a new task into the selected date section in the journal file."""
    clean_title = title.strip()
    if not clean_title:
        return False

    selected_date = target_date or datetime.now()
    date_header = f"## {selected_date.strftime('%d/%m/%Y')}"

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"

        new_task_line = f"- {clean_title} -- {state}\n"

        section_index = None
        for idx, line in enumerate(lines):
            if line.strip() == date_header:
                section_index = idx
                break

        if section_index is not None:
            insert_idx = len(lines)
            for idx in range(section_index + 1, len(lines)):
                if parse_date(lines[idx]) is not None:
                    insert_idx = idx
                    break
            lines.insert(insert_idx, new_task_line)
        else:
            if lines and lines[-1].strip() != "":
                lines.append("\n")
            lines.append(f"{date_header}\n")
            lines.append(new_task_line)

        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return True
    except Exception:
        return False
