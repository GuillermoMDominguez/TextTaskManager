"""Journal parsing and persistence helpers."""

import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from tm_config import DEFAULT_STATE, STATE_ALIASES, VALID_STATES
from tm_models import Subtask, Task


class JournalError(Exception):
    """Base error for journal operations."""


class JournalFileNotFoundError(JournalError):
    """Raised when the journal file cannot be found."""


class JournalReadError(JournalError):
    """Raised when the journal file cannot be parsed or read."""


def split_comments(text: str) -> List[str]:
    """Split a text by ':' into separate comments, filtering empty ones."""
    return [c.strip() for c in text.split(":") if c.strip()]


def append_unique_comments(target: List[str], new_comments: List[str]) -> None:
    """Append comments preserving order while avoiding duplicates."""
    for comment in new_comments:
        if comment not in target:
            target.append(comment)


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
        append_unique_comments(comments, split_comments(title_part[idx + 1 :]))
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
                append_unique_comments(comments, split_comments(remaining[1:]))
            elif remaining:
                append_unique_comments(comments, split_comments(remaining))
        else:
            append_unique_comments(comments, split_comments(part))

    task.state = current_state
    task.comments = comments
    return task


def parse_subtask_line(line: str) -> Optional[Subtask]:
    """Parse a single subtask line and extract title and state."""
    stripped = line.strip()
    if not stripped.startswith("+"):
        return None

    content = stripped[1:].strip()
    if not content:
        return None

    subtask = Subtask(title="")
    current_state = DEFAULT_STATE

    parts = re.split(r"\s*(?:--|->)\s*", content)
    subtask.title = parts[0].strip()
    if not subtask.title:
        return None

    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue

        for alias, canonical in STATE_ALIASES.items():
            if part.upper().startswith(alias):
                current_state = canonical
                break
        else:
            for state in VALID_STATES:
                if part.upper().startswith(state):
                    current_state = state
                    break

    subtask.state = current_state
    return subtask


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
                        append_unique_comments(last_task.comments, split_comments(stripped[1:]))
                    continue

                if stripped.startswith("+"):
                    if last_task is not None:
                        subtask = parse_subtask_line(line)
                        if subtask and subtask.title:
                            subtask.source_line = line_number
                            last_task.subtasks.append(subtask)
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

    except FileNotFoundError as exc:
        raise JournalFileNotFoundError(f"File not found: {filepath}") from exc
    except Exception as exc:
        raise JournalReadError(f"Error reading file: {exc}") from exc

    return tasks_by_date


def _read_lines(filepath: str) -> List[str]:
    with open(filepath, "r", encoding="utf-8") as file_handle:
        return file_handle.readlines()


def _write_lines(filepath: str, lines: List[str]) -> None:
    with open(filepath, "w", encoding="utf-8") as file_handle:
        file_handle.writelines(lines)


def _task_line_indent(line: str, marker: str) -> str:
    match = re.match(rf"^(\s*)\{marker}\s*", line)
    return match.group(1) if match else ""


def _find_task_block_bounds(lines: List[str], task: Task) -> Optional[Tuple[int, int]]:
    if task.source_line is None:
        return None

    start_idx = task.source_line - 1
    if start_idx < 0 or start_idx >= len(lines):
        return None

    end_idx = start_idx + 1
    while end_idx < len(lines):
        stripped = lines[end_idx].strip()
        if stripped.startswith("##"):
            break
        if stripped.startswith("-") and not stripped.startswith("--"):
            break
        end_idx += 1

    return start_idx, end_idx


def _find_parent_task_start(lines: List[str], subtask: Subtask) -> Optional[int]:
    if subtask.source_line is None:
        return None

    current_idx = subtask.source_line - 2
    while current_idx >= 0:
        stripped = lines[current_idx].strip()
        if stripped.startswith("-") and not stripped.startswith("--"):
            return current_idx
        if stripped.startswith("##"):
            break
        current_idx -= 1
    return None


def _find_note_line_index(lines: List[str], task: Task, note_index: int) -> Optional[int]:
    bounds = _find_task_block_bounds(lines, task)
    if bounds is None:
        return None

    start_idx, end_idx = bounds
    current_note = 0
    for line_idx in range(start_idx + 1, end_idx):
        if lines[line_idx].strip().startswith(":"):
            if current_note == note_index:
                return line_idx
            current_note += 1
    return None


def _render_task_block(task: Task, state_override: Optional[str] = None) -> List[str]:
    lines = [f"- {task.title} -- {state_override or task.state}\n"]
    lines.extend(f": {comment}\n" for comment in task.comments)
    lines.extend(f"+ {subtask.title} -- {subtask.state}\n" for subtask in task.subtasks)
    return lines


def _insert_task_block(lines: List[str], block_lines: List[str], target_date: Optional[datetime]) -> List[str]:
    target_header = f"## {target_date.strftime('%d/%m/%Y')}" if target_date else None

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    if target_header is None:
        insert_idx = len(lines)
        for idx, line in enumerate(lines):
            if parse_date(line) is not None:
                insert_idx = idx
                break
        lines[insert_idx:insert_idx] = block_lines
        return lines

    section_index = None
    for idx, line in enumerate(lines):
        if line.strip() == target_header:
            section_index = idx
            break

    if section_index is not None:
        insert_idx = len(lines)
        for idx in range(section_index + 1, len(lines)):
            if parse_date(lines[idx]) is not None:
                insert_idx = idx
                break
        lines[insert_idx:insert_idx] = block_lines
        return lines

    if lines and lines[-1].strip() != "":
        lines.append("\n")
    lines.append(f"{target_header}\n")
    lines.extend(block_lines)
    return lines


def update_task_state_in_file(filepath: str, task: Task, new_state: str) -> bool:
    """Persist a task state change in the journal file."""
    if task.source_line is None:
        return False

    try:
        lines = _read_lines(filepath)

        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        original_line = lines[line_index]
        indent = _task_line_indent(original_line, "-")

        new_line = f"{indent}- {task.title} -- {new_state}\n"

        lines[line_index] = new_line

        _write_lines(filepath, lines)

        return True
    except Exception:
        return False


def add_note_to_task_in_file(filepath: str, task: Task, note: str) -> bool:
    """Persist a note line (': ...') inside a task block."""
    if task.source_line is None:
        return False

    clean_note = note.strip()
    if not clean_note:
        return False

    try:
        lines = _read_lines(filepath)

        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        task_line = lines[line_index]
        indent = _task_line_indent(task_line, "-")

        insert_idx = line_index + 1
        while insert_idx < len(lines):
            stripped = lines[insert_idx].strip()
            if stripped.startswith("##"):
                break
            if stripped.startswith("-") and not stripped.startswith("--"):
                break
            insert_idx += 1

        lines.insert(insert_idx, f"{indent}: {clean_note}\n")

        _write_lines(filepath, lines)

        return True
    except Exception:
        return False


def update_subtask_state_in_file(filepath: str, subtask: Subtask, new_state: str) -> bool:
    """Persist a subtask state change in the journal file."""
    if subtask.source_line is None:
        return False

    try:
        lines = _read_lines(filepath)

        line_index = subtask.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        original_line = lines[line_index]
        indent = _task_line_indent(original_line, "+")

        lines[line_index] = f"{indent}+ {subtask.title} -- {new_state}\n"

        _write_lines(filepath, lines)

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
        lines = _read_lines(filepath)

        new_task_line = f"- {clean_title} -- {state}\n"
        lines = _insert_task_block(lines, [new_task_line], selected_date)
        _write_lines(filepath, lines)

        return True
    except Exception:
        return False


def edit_task_title_in_file(filepath: str, task: Task, new_title: str) -> bool:
    """Rename a parent task while keeping state and children."""
    clean_title = new_title.strip()
    if task.source_line is None or not clean_title:
        return False

    try:
        lines = _read_lines(filepath)
        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False
        indent = _task_line_indent(lines[line_index], "-")
        lines[line_index] = f"{indent}- {clean_title} -- {task.state}\n"
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def edit_subtask_title_in_file(filepath: str, subtask: Subtask, new_title: str) -> bool:
    """Rename a subtask while keeping its state."""
    clean_title = new_title.strip()
    if subtask.source_line is None or not clean_title:
        return False

    try:
        lines = _read_lines(filepath)
        line_index = subtask.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False
        indent = _task_line_indent(lines[line_index], "+")
        lines[line_index] = f"{indent}+ {clean_title} -- {subtask.state}\n"
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def delete_task_in_file(filepath: str, task: Task) -> bool:
    """Delete a task block, including notes and subtasks."""
    try:
        lines = _read_lines(filepath)
        bounds = _find_task_block_bounds(lines, task)
        if bounds is None:
            return False
        start_idx, end_idx = bounds
        del lines[start_idx:end_idx]
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def delete_subtask_in_file(filepath: str, subtask: Subtask) -> bool:
    """Delete a single subtask line from its parent block."""
    if subtask.source_line is None:
        return False

    try:
        lines = _read_lines(filepath)
        line_index = subtask.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False
        del lines[line_index]
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def delete_note_in_file(filepath: str, task: Task, note_index: int) -> bool:
    """Delete a task note by zero-based index."""
    try:
        lines = _read_lines(filepath)
        line_index = _find_note_line_index(lines, task, note_index)
        if line_index is None:
            return False
        del lines[line_index]
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def edit_note_in_file(filepath: str, task: Task, note_index: int, new_note: str) -> bool:
    """Edit a task note by zero-based index."""
    clean_note = new_note.strip()
    if not clean_note:
        return False

    try:
        lines = _read_lines(filepath)
        line_index = _find_note_line_index(lines, task, note_index)
        if line_index is None:
            return False

        original_line = lines[line_index]
        indent = _task_line_indent(original_line, ":")
        lines[line_index] = f"{indent}: {clean_note}\n"
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def move_task_to_date_in_file(filepath: str, task: Task, target_date: datetime) -> bool:
    """Move a full task block to another date section."""
    try:
        lines = _read_lines(filepath)
        bounds = _find_task_block_bounds(lines, task)
        if bounds is None:
            return False
        start_idx, end_idx = bounds
        block_lines = lines[start_idx:end_idx]
        del lines[start_idx:end_idx]
        lines = _insert_task_block(lines, block_lines, target_date)
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def duplicate_task_in_file(filepath: str, task: Task, target_date: Optional[datetime] = None) -> bool:
    """Duplicate a task with its notes and subtasks."""
    try:
        lines = _read_lines(filepath)
        block_lines = _render_task_block(task)
        lines = _insert_task_block(lines, block_lines, target_date or task.date)
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def mark_all_subtasks_done_in_file(filepath: str, task: Task) -> bool:
    """Mark all subtasks in a task block as DONE."""
    try:
        lines = _read_lines(filepath)
        bounds = _find_task_block_bounds(lines, task)
        if bounds is None:
            return False
        start_idx, end_idx = bounds
        updated_any = False
        for line_index in range(start_idx + 1, end_idx):
            stripped = lines[line_index].strip()
            if not stripped.startswith("+"):
                continue
            parsed = parse_subtask_line(lines[line_index])
            if parsed is None:
                continue
            indent = _task_line_indent(lines[line_index], "+")
            lines[line_index] = f"{indent}+ {parsed.title} -- DONE\n"
            updated_any = True
        if not updated_any:
            return False
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def archive_finished_tasks_in_file(
    filepath: str,
    archive_path: str,
    before_date: Optional[datetime] = None,
) -> int:
    """Move finished task blocks into an archive journal and return moved count."""
    try:
        tasks_by_date = parse_journal(filepath)
        candidates: List[Task] = []
        for date, tasks in tasks_by_date.items():
            for task in tasks:
                if not task.is_finished():
                    continue
                if before_date is not None and date is not None and date > before_date:
                    continue
                candidates.append(task)

        if not candidates:
            return 0

        lines = _read_lines(filepath)
        grouped_blocks: "OrderedDict[Optional[datetime], List[List[str]]]" = OrderedDict()

        for task in sorted(candidates, key=lambda item: item.source_line or 0, reverse=True):
            bounds = _find_task_block_bounds(lines, task)
            if bounds is None:
                continue
            start_idx, end_idx = bounds
            block_lines = lines[start_idx:end_idx]
            grouped_blocks.setdefault(task.date, []).insert(0, block_lines)
            del lines[start_idx:end_idx]

        _write_lines(filepath, lines)

        archive_file = Path(archive_path)
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_lines = _read_lines(archive_path) if archive_file.exists() else []

        for date, blocks in grouped_blocks.items():
            if archive_lines and archive_lines[-1].strip() != "":
                archive_lines.append("\n")
            if date is not None:
                archive_lines.append(f"## {date.strftime('%d/%m/%Y')}\n")
            for block in blocks:
                archive_lines.extend(block)

        _write_lines(archive_path, archive_lines)
        return len(candidates)
    except Exception:
        return 0
