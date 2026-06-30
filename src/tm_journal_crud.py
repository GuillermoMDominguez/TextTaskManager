"""Journal CRUD — all task/subtask/note mutation operations on journal files."""

import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .tm_config import DEFAULT_STATE, STATE_ALIASES, VALID_STATES
from .tm_models import Subtask, Task
from .tm_journal_hooks import _notify_post_write, file_lock
from .tm_journal_parser import (
    _find_note_line_index,
    _find_task_block_bounds,
    _insert_task_block,
    _parse_due_value,
    _parse_priority_value,
    _render_subtask_line,
    _render_task_block,
    _render_task_line,
    _task_line_indent,
    parse_journal,
    parse_subtask_line,
    parse_date,
)
from .tm_journal_writer import _read_lines, _write_lines, write_journal


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

        raw_content = original_line.strip()[1:].strip()
        raw_parts = re.split(r"\s*(?:--|->)\s*", raw_content)
        raw_title = raw_parts[0].strip()
        if ":" in raw_title:
            raw_title = raw_title[:raw_title.find(":")].strip()

        continuation_tags = []
        lines_to_remove = []
        for j in range(line_index + 1, len(lines)):
            cline = lines[j]
            if not cline or not cline[0].isspace():
                break
            cstripped = cline.strip()
            if cstripped.startswith("--") and not cstripped.startswith("---"):
                meta = cstripped[2:].strip()
                if meta:
                    is_meta = False
                    for state in VALID_STATES:
                        if meta.upper() == state:
                            is_meta = True
                            break
                    if not is_meta:
                        for alias in STATE_ALIASES:
                            if meta.upper() == alias:
                                is_meta = True
                                break
                    if not is_meta:
                        if re.match(r"^(?:due|priority|recur|spent|time|blockedby|blocks|jira|notes)\s*[:=]", meta, re.IGNORECASE):
                            is_meta = True
                    if is_meta:
                        lines_to_remove.append(j)
                        continue
                    else:
                        break
                else:
                    lines_to_remove.append(j)
                    continue
            elif cstripped and cstripped[0] == "#" and all(
                t.startswith("#") for t in cstripped.split()
            ):
                continuation_tags.extend(cstripped.split())
                lines_to_remove.append(j)
                continue
            else:
                break

        for j in sorted(lines_to_remove, reverse=True):
            del lines[j]

        title_with_tags = raw_title
        if continuation_tags:
            title_with_tags = raw_title + " " + " ".join(continuation_tags)

        new_line = _render_task_line(
            title_with_tags, new_state, task.due_date, task.priority, indent, task.recurrence,
            jira_key=task.jira_key, linked_notes=task.linked_notes,
        )

        if task.time_spent:
            from .tm_features import format_time_spent
            new_line = new_line.rstrip("\n") + f" -- spent:{format_time_spent(task.time_spent)}\n"

        for b in (task.blocked_by or []):
            new_line = new_line.rstrip("\n") + f" -- blockedby:{b}\n"
        for b in (task.blocks or []):
            new_line = new_line.rstrip("\n") + f" -- blocks:{b}\n"

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
        child_indent = indent + "    "

        insert_idx = line_index + 1
        while insert_idx < len(lines):
            stripped = lines[insert_idx].strip()
            if not stripped:
                break
            if stripped.startswith("##"):
                break
            if stripped.startswith("-") and not stripped.startswith("--"):
                break
            if stripped.startswith("+"):
                break
            insert_idx += 1

        lines.insert(insert_idx, f"{child_indent}: {clean_note}\n")

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

        parts = [f"{indent}+ {subtask.title} -- {new_state}"]
        if subtask.due_date is not None:
            parts.append(f"due:{subtask.due_date.strftime('%d/%m/%Y')}")
        lines[line_index] = " -- ".join(parts) + "\n"

        _write_lines(filepath, lines)

        return True
    except Exception:
        return False


def add_task_to_file(
    filepath: str,
    title: str,
    state: str = DEFAULT_STATE,
    target_date: Optional[datetime] = None,
    due_date: Optional[datetime] = None,
    priority: Optional[str] = None,
    recurrence: Optional[str] = None,
    jira_key: Optional[str] = None,
) -> bool:
    """Append a new task into the selected date section in the journal file."""
    clean_title = title.strip()
    if not clean_title:
        return False

    selected_date = target_date or datetime.now()

    try:
        lines = _read_lines(filepath)

        new_task_line = _render_task_line(clean_title, state, due_date, priority, recurrence=recurrence, jira_key=jira_key)
        lines = _insert_task_block(lines, [new_task_line], selected_date)
        _write_lines(filepath, lines)

        return True
    except Exception:
        return False


def add_subtask_to_file(filepath: str, parent_title: str, subtask_title: str, state: str = DEFAULT_STATE) -> bool:
    """Add a subtask line right after the matching parent task (or its existing subtasks)."""
    try:
        lines = _read_lines(filepath)
        subtask_line = f"+ {subtask_title} -- {state}\n"

        insert_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("-") and not stripped.startswith("--") and parent_title in line:
                insert_idx = i + 1
                while insert_idx < len(lines):
                    child = lines[insert_idx].strip()
                    if child.startswith("+") or child.startswith(":"):
                        insert_idx += 1
                    else:
                        break
                break

        if insert_idx is None:
            return False

        lines.insert(insert_idx, subtask_line)
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def add_subtask_to_task(filepath: str, task: "Task", subtask_title: str, state: str = DEFAULT_STATE) -> bool:
    """Add a subtask to a task using source_line for reliable placement."""
    if task.source_line is None:
        return False
    try:
        lines = _read_lines(filepath)
        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        indent = "    "
        subtask_line = f"{indent}+ {subtask_title} -- {state}\n"

        insert_idx = line_index + 1
        while insert_idx < len(lines):
            cline = lines[insert_idx]
            if not cline or not cline[0].isspace():
                break
            cstripped = cline.strip()
            if not cstripped:
                peek = insert_idx + 1
                while peek < len(lines) and not lines[peek].strip():
                    peek += 1
                if peek < len(lines) and lines[peek] and lines[peek][0].isspace():
                    insert_idx += 1
                    continue
                break
            insert_idx += 1

        lines.insert(insert_idx, subtask_line)
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def edit_task_title_in_file(filepath: str, task: Task, new_title: str) -> bool:
    """Rename a parent task while keeping state, children, and metadata."""
    clean_title = new_title.strip()
    if task.source_line is None or not clean_title:
        return False

    try:
        lines = _read_lines(filepath)
        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False
        indent = _task_line_indent(lines[line_index], "-")
        new_line = _render_task_line(clean_title, task.state, task.due_date, task.priority, indent, task.recurrence, jira_key=task.jira_key, linked_notes=task.linked_notes)
        if task.time_spent:
            from .tm_features import format_time_spent
            new_line = new_line.rstrip("\n") + f" -- spent:{format_time_spent(task.time_spent)}\n"
        for b in (task.blocked_by or []):
            new_line = new_line.rstrip("\n") + f" -- blockedby:{b}\n"
        for b in (task.blocks or []):
            new_line = new_line.rstrip("\n") + f" -- blocks:{b}\n"
        lines[line_index] = new_line
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def update_dependency_references(filepath: str, old_title: str, new_title: str) -> None:
    """Update blockedby:/blocks: references when a task is renamed."""
    if not old_title or not new_title or old_title == new_title:
        return
    try:
        lines = _read_lines(filepath)
        changed = False
        for i, line in enumerate(lines):
            new_line = re.sub(
                r"((?:blockedby|blocks)\s*:\s*)" + re.escape(old_title) + r"(?=\s*(?:--|$|\n))",
                lambda m: m.group(1) + new_title,
                line,
                flags=re.IGNORECASE,
            )
            if new_line != line:
                lines[i] = new_line
                changed = True
        if changed:
            _write_lines(filepath, lines)
    except Exception:
        pass


def update_task_metadata_in_file(
    filepath: str,
    task: Task,
    due_date: Optional[datetime],
    priority: Optional[str],
    recurrence: Optional[str] = None,
    jira_key: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    """Update due date, priority, and/or recurrence metadata for a parent task."""
    if task.source_line is None:
        return False

    if recurrence is None:
        effective_recurrence = task.recurrence
    elif recurrence == "":
        effective_recurrence = None
    else:
        effective_recurrence = recurrence

    if jira_key is None:
        effective_jira_key = task.jira_key
    elif jira_key == "":
        effective_jira_key = None
    else:
        effective_jira_key = jira_key

    if notes is None:
        effective_notes = task.linked_notes
    elif notes == "":
        effective_notes = []
    else:
        effective_notes = [n.strip() for n in notes.split(",") if n.strip()]

    try:
        lines = _read_lines(filepath)
        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        indent = _task_line_indent(lines[line_index], "-")
        lines[line_index] = _render_task_line(task.title, task.state, due_date, priority, indent, effective_recurrence, jira_key=effective_jira_key, linked_notes=effective_notes or None)
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


def update_subtask_metadata_in_file(
    filepath: str,
    subtask: "Subtask",
    due_date: Optional[datetime] = None,
    priority: Optional[str] = None,
    clear_due: bool = False,
    clear_priority: bool = False,
    notes: Optional[str] = None,
) -> bool:
    """Update due date and/or priority on a subtask line."""
    if subtask.source_line is None:
        return False

    effective_due = None if clear_due else (due_date if due_date is not None else subtask.due_date)
    effective_priority = None if clear_priority else (priority if priority is not None else subtask.priority)
    effective_notes = None if notes is None else notes
    if effective_notes is None and subtask.linked_notes:
        effective_notes = ",".join(subtask.linked_notes)

    try:
        lines = _read_lines(filepath)
        line_index = subtask.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False
        indent = _task_line_indent(lines[line_index], "+")
        lines[line_index] = _render_subtask_line(subtask.title, subtask.state, effective_due, effective_priority, indent, effective_notes)
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def add_note_to_subtask_in_file(filepath: str, subtask: "Subtask", note: str) -> bool:
    """Add a note line after the subtask line."""
    clean_note = note.strip()
    if subtask.source_line is None or not clean_note:
        return False

    try:
        lines = _read_lines(filepath)
        line_index = subtask.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        base_indent = _task_line_indent(lines[line_index], "+")
        note_indent = base_indent + "    "

        insert_idx = line_index + 1
        while insert_idx < len(lines):
            cline = lines[insert_idx]
            if not cline.strip():
                break
            stripped = cline.strip()
            if stripped.startswith("+") or stripped.startswith("-"):
                line_indent = len(cline) - len(cline.lstrip())
                base_len = len(base_indent)
                if line_indent <= base_len:
                    break
            if stripped.startswith(":"):
                line_indent = len(cline) - len(cline.lstrip())
                if line_indent > len(base_indent):
                    insert_idx += 1
                    continue
                else:
                    break
            if len(cline) > len(base_indent) + 4 and cline[0].isspace():
                insert_idx += 1
                continue
            break

        lines.insert(insert_idx, f"{note_indent}: {clean_note}\n")
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
