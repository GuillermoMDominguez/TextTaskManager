"""Journal parsing and persistence helpers."""

import os
import re
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .tm_config import DEFAULT_STATE, PRIORITY_ALIASES, RECURRENCE_ALIASES, STATE_ALIASES, VALID_PRIORITIES, VALID_RECURRENCES, VALID_STATES
from .tm_models import Subtask, Task


# ─── File lock (shared with tm_sync to prevent concurrent access) ──────────────
# Acquire this lock before writing to any journal file.
file_lock = threading.Lock()


# ─── Post-write hooks ──────────────────────────────────────────────────────────
# Registered callbacks are called after any journal write operation.
# Signature: callback() -> None

_post_write_hooks: List[Callable[[], None]] = []


def register_post_write_hook(callback: Callable[[], None]) -> None:
    """Register a callback to be invoked after any journal file write."""
    _post_write_hooks.append(callback)


def _notify_post_write() -> None:
    """Invoke all registered post-write hooks."""
    for hook in _post_write_hooks:
        try:
            hook()
        except Exception:
            pass  # Hooks must never crash the main app


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


def _parse_priority_value(raw: str) -> Optional[str]:
    normalized = raw.strip().upper()
    if normalized in VALID_PRIORITIES:
        return normalized
    return PRIORITY_ALIASES.get(normalized)


def _parse_due_value(raw: str) -> Optional[datetime]:
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y")
    except ValueError:
        return None


def _parse_recurrence_value(raw: str) -> Optional[str]:
    normalized = raw.strip().lower()
    if normalized in VALID_RECURRENCES:
        return normalized
    return RECURRENCE_ALIASES.get(normalized.upper())


def _apply_task_metadata(task: Task, chunk: str) -> bool:
    due_match = re.match(r"^(?:due|d)\s*[:=]\s*(\d{1,2}/\d{1,2}/\d{4})$", chunk, re.IGNORECASE)
    if due_match:
        due_date = _parse_due_value(due_match.group(1))
        if due_date is not None:
            task.due_date = due_date
            return True
        return False

    priority_match = re.match(r"^(?:priority|prio|p)\s*[:=]\s*([A-Za-z]+)$", chunk, re.IGNORECASE)
    if priority_match:
        priority = _parse_priority_value(priority_match.group(1))
        if priority is not None:
            task.priority = priority
            return True
        return False

    recur_match = re.match(r"^(?:recur|recurrence|rec|r)\s*[:=]\s*([A-Za-z]+)$", chunk, re.IGNORECASE)
    if recur_match:
        recurrence = _parse_recurrence_value(recur_match.group(1))
        if recurrence is not None:
            task.recurrence = recurrence
            return True
        return False

    spent_match = re.match(r"^(?:spent|time)\s*[:=]\s*(\S+)$", chunk, re.IGNORECASE)
    if spent_match:
        from .tm_features import parse_time_spent
        minutes = parse_time_spent(spent_match.group(1))
        if minutes is not None:
            task.time_spent = (task.time_spent or 0) + minutes
            return True
        return False

    blockedby_match = re.match(r"^blockedby\s*[:=]\s*(.+)$", chunk, re.IGNORECASE)
    if blockedby_match:
        task.blocked_by.append(blockedby_match.group(1).strip())
        return True

    blocks_match = re.match(r"^blocks\s*[:=]\s*(.+)$", chunk, re.IGNORECASE)
    if blocks_match:
        task.blocks.append(blocks_match.group(1).strip())
        return True

    return False


def _apply_subtask_metadata(subtask, chunk: str) -> bool:
    """Apply metadata key:value to a subtask (supports due date)."""
    due_match = re.match(r"^(?:due|d)\s*[:=]\s*(\d{1,2}/\d{1,2}/\d{4})$", chunk, re.IGNORECASE)
    if due_match:
        due_date = _parse_due_value(due_match.group(1))
        if due_date is not None:
            subtask.due_date = due_date
            return True
        return False
    return False


def _render_task_line(
    title: str,
    state: str,
    due_date: Optional[datetime],
    priority: Optional[str],
    indent: str = "",
    recurrence: Optional[str] = None,
) -> str:
    parts = [f"{indent}- {title} -- {state}"]
    if due_date is not None:
        parts.append(f"due:{due_date.strftime('%d/%m/%Y')}")
    if priority:
        parts.append(f"priority:{priority}")
    if recurrence:
        parts.append(f"recur:{recurrence}")
    return " -- ".join(parts) + "\n"


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

        if _apply_task_metadata(task, part):
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
            if _apply_task_metadata(task, remaining):
                continue
            if remaining.startswith(":"):
                append_unique_comments(comments, split_comments(remaining[1:]))
            elif remaining:
                append_unique_comments(comments, split_comments(remaining))
        else:
            if _apply_task_metadata(task, part):
                continue
            append_unique_comments(comments, split_comments(part))

    task.state = current_state
    task.comments = comments
    return task


def parse_subtask_line(line: str) -> Optional[Subtask]:
    """Parse a single subtask line and extract title, state, and due date."""
    stripped = line.strip()
    if not stripped.startswith("+"):
        return None

    content = stripped[1:].strip()
    if not content:
        return None

    subtask = Subtask(title="")
    current_state = DEFAULT_STATE

    parts = re.split(r"\s*(?:--|->)\s*", content)
    raw_title = parts[0].strip()

    # Extract inline [priority=X] from title
    inline_prio = re.search(r"\[(?:priority|prio|p)\s*=\s*([A-Za-z]+)\]", raw_title, re.IGNORECASE)
    if inline_prio:
        prio_val = _parse_priority_value(inline_prio.group(1))
        if prio_val is not None:
            subtask.priority = prio_val
        raw_title = raw_title[:inline_prio.start()].rstrip() + raw_title[inline_prio.end():]
        raw_title = raw_title.strip()

    subtask.title = raw_title
    if not subtask.title:
        return None

    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue

        # Check for due date in subtask metadata
        due_match = re.match(r"^(?:due|d)\s*[:=]\s*(\d{1,2}/\d{1,2}/\d{4})$", part, re.IGNORECASE)
        if due_match:
            subtask.due_date = _parse_due_value(due_match.group(1))
            continue

        # Check for priority in subtask metadata
        prio_match = re.match(r"^(?:priority|prio|p)\s*[:=]\s*([A-Za-z]+)$", part, re.IGNORECASE)
        if prio_match:
            prio_val = _parse_priority_value(prio_match.group(1))
            if prio_val is not None:
                subtask.priority = prio_val
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
    last_subtask: Optional[Subtask] = None
    last_element = None  # 'task', 'subtask', 'note', 'subnote' — for continuation lines

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                date = parse_date(line)
                if date:
                    current_date = date
                    last_task = None
                    last_subtask = None
                    if current_date not in tasks_by_date:
                        tasks_by_date[current_date] = []
                    continue

                stripped = line.strip()

                # Metadata continuation line: starts with -- (on its own line)
                if stripped.startswith("--") and not stripped.startswith("---"):
                    meta_content = stripped[2:].strip()
                    if meta_content:
                        # Determine target: last subtask or last task
                        target = last_subtask if last_subtask is not None else last_task
                        if target is not None:
                            # Try as state first
                            state_applied = False
                            for state in VALID_STATES:
                                if meta_content.upper() == state:
                                    target.state = state
                                    state_applied = True
                                    break
                            if not state_applied:
                                for alias, canonical in STATE_ALIASES.items():
                                    if meta_content.upper() == alias:
                                        target.state = canonical
                                        state_applied = True
                                        break
                            if not state_applied:
                                if last_subtask is not None:
                                    _apply_subtask_metadata(last_subtask, meta_content)
                                elif last_task is not None:
                                    _apply_task_metadata(last_task, meta_content)
                    continue

                if stripped.startswith(":"):
                    note_text = stripped[1:].strip()
                    if note_text:
                        if last_subtask is not None:
                            last_subtask.comments.append(note_text)
                            last_element = 'subnote'
                        elif last_task is not None:
                            last_task.comments.append(note_text)
                            last_element = 'note'
                    continue

                if stripped.startswith("+"):
                    if last_task is not None:
                        subtask = parse_subtask_line(line)
                        if subtask and subtask.title:
                            subtask.source_line = line_number
                            last_task.subtasks.append(subtask)
                            last_subtask = subtask
                            last_element = 'subtask'
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
                        last_subtask = None
                        last_element = 'task'
                    continue

                # Unrecognized lines: continuation of previous element
                if stripped and last_task is not None:
                    if last_element == 'note' and last_task.comments:
                        # Multiline note continuation
                        last_task.comments[-1] += "\n" + stripped
                    elif last_element == 'subnote' and last_subtask and last_subtask.comments:
                        last_subtask.comments[-1] += "\n" + stripped
                    elif last_element == 'subtask' and last_subtask is not None:
                        last_subtask.title = last_subtask.title.rstrip() + " " + stripped
                    else:
                        # Title continuation (tags on separate line, etc.)
                        last_task.title = last_task.title.rstrip() + " " + stripped

    except FileNotFoundError as exc:
        raise JournalFileNotFoundError(f"File not found: {filepath}") from exc
    except Exception as exc:
        raise JournalReadError(f"Error reading file: {exc}") from exc

    return tasks_by_date


def _read_lines(filepath: str) -> List[str]:
    with open(filepath, "r", encoding="utf-8") as file_handle:
        return file_handle.readlines()


def _write_lines(filepath: str, lines: List[str]) -> None:
    """Atomically write lines to a journal file (write-tmp + rename)."""
    tmp = filepath + ".tmp"
    with file_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, filepath)
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    _notify_post_write()


def write_journal(filepath: str, content: str) -> None:
    """Atomically write full text content to a journal file.

    Use this instead of Path(...).write_text() for any journal writes.
    Ensures atomic write (tmp+rename) and triggers post-write hooks.
    """
    tmp = filepath + ".tmp"
    with file_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, filepath)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    _notify_post_write()


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
    lines = [
        _render_task_line(
            title=task.title,
            state=state_override or task.state,
            due_date=task.due_date,
            priority=task.priority,
            recurrence=task.recurrence,
        )
    ]
    for comment in task.comments:
        # Multiline notes: first line gets :, continuation lines are indented
        note_lines = comment.split("\n")
        lines.append(f": {note_lines[0]}\n")
        for continuation in note_lines[1:]:
            lines.append(f"  {continuation}\n")
    for subtask in task.subtasks:
        lines.append(f"+ {subtask.title} -- {subtask.state}\n")
        for scomment in getattr(subtask, "comments", []):
            snote_lines = scomment.split("\n")
            lines.append(f"    : {snote_lines[0]}\n")
            for scont in snote_lines[1:]:
                lines.append(f"      {scont}\n")
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
    """Persist a task state change in the journal file.

    Handles both inline and multiline metadata formats. When a task uses
    multiline continuation lines (-- STATE, -- due:, etc.), those lines are
    removed and the metadata is consolidated into the task line.
    """
    if task.source_line is None:
        return False

    try:
        lines = _read_lines(filepath)

        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        original_line = lines[line_index]
        indent = _task_line_indent(original_line, "-")

        # Extract raw title from the file line (not task.title which may have appended tags)
        raw_content = original_line.strip()[1:].strip()  # Remove leading -
        raw_parts = re.split(r"\s*(?:--|->)\s*", raw_content)
        raw_title = raw_parts[0].strip()
        # Remove inline comments after ':'
        if ":" in raw_title:
            raw_title = raw_title[:raw_title.find(":")].strip()

        # Collect tags from continuation lines and remove metadata continuations
        continuation_tags = []
        lines_to_remove = []
        for j in range(line_index + 1, len(lines)):
            cline = lines[j]
            if not cline or not cline[0].isspace():
                break
            cstripped = cline.strip()
            # Metadata continuation: -- something
            if cstripped.startswith("--") and not cstripped.startswith("---"):
                meta = cstripped[2:].strip()
                if meta:
                    # Is it a state, due, priority, recur, spent, blockedby, blocks?
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
                        if re.match(r"^(?:due|priority|recur|spent|time|blockedby|blocks)\s*[:=]", meta, re.IGNORECASE):
                            is_meta = True
                    if is_meta:
                        lines_to_remove.append(j)
                        continue
                    else:
                        # Unknown -- line, keep it
                        break
                else:
                    lines_to_remove.append(j)
                    continue
            # Tag-only continuation line (e.g. "    #bugs #qr")
            elif cstripped and cstripped[0] == "#" and all(
                t.startswith("#") for t in cstripped.split()
            ):
                continuation_tags.extend(cstripped.split())
                lines_to_remove.append(j)
                continue
            else:
                break

        # Remove collected continuation lines (in reverse to preserve indices)
        for j in sorted(lines_to_remove, reverse=True):
            del lines[j]

        # Build the title with tags
        title_with_tags = raw_title
        if continuation_tags:
            title_with_tags = raw_title + " " + " ".join(continuation_tags)

        new_line = _render_task_line(
            title_with_tags, new_state, task.due_date, task.priority, indent, task.recurrence
        )

        # Add spent if task had time tracked
        if task.time_spent:
            from .tm_features import format_time_spent
            new_line = new_line.rstrip("\n") + f" -- spent:{format_time_spent(task.time_spent)}\n"

        # Add blockers
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
    """Persist a note line (': ...') inside a task block.

    Inserts the note AFTER existing metadata/notes but BEFORE subtasks,
    so the parser associates it with the parent task (not a subtask).
    """
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
        # Child content (notes, subtasks) is indented one level deeper
        child_indent = indent + "    "

        # Walk forward past metadata (-- lines), tag-only lines, and existing
        # notes (: lines), but STOP at subtask lines (+) or new tasks (-).
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
                # Subtask — insert BEFORE this line
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
) -> bool:
    """Append a new task into the selected date section in the journal file."""
    clean_title = title.strip()
    if not clean_title:
        return False

    selected_date = target_date or datetime.now()

    try:
        lines = _read_lines(filepath)

        new_task_line = _render_task_line(clean_title, state, due_date, priority, recurrence=recurrence)
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

        # Find the parent task line
        insert_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("-") and not stripped.startswith("--") and parent_title in line:
                # Found parent — scan forward past its children
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

        # Determine indentation: scan existing content to match style
        indent = "    "
        subtask_line = f"{indent}+ {subtask_title} -- {state}\n"

        # Scan forward past all task block content (metadata, notes, subtasks)
        insert_idx = line_index + 1
        while insert_idx < len(lines):
            cline = lines[insert_idx]
            if not cline or not cline[0].isspace():
                break
            cstripped = cline.strip()
            # Empty indented line — peek ahead
            if not cstripped:
                # Check if next non-empty line is still part of block
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
        lines[line_index] = _render_task_line(clean_title, task.state, task.due_date, task.priority, indent, task.recurrence)
        _write_lines(filepath, lines)
        return True
    except Exception:
        return False


def update_task_metadata_in_file(
    filepath: str,
    task: Task,
    due_date: Optional[datetime],
    priority: Optional[str],
    recurrence: Optional[str] = None,
) -> bool:
    """Update due date, priority, and/or recurrence metadata for a parent task.

    recurrence=None means keep existing, recurrence="" means remove,
    recurrence="weekly" etc means set that value.
    """
    if task.source_line is None:
        return False

    # Determine effective recurrence
    if recurrence is None:
        effective_recurrence = task.recurrence  # keep existing
    elif recurrence == "":
        effective_recurrence = None  # remove
    else:
        effective_recurrence = recurrence  # set new value

    try:
        lines = _read_lines(filepath)
        line_index = task.source_line - 1
        if line_index < 0 or line_index >= len(lines):
            return False

        indent = _task_line_indent(lines[line_index], "-")
        lines[line_index] = _render_task_line(task.title, task.state, due_date, priority, indent, effective_recurrence)
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


def read_journal_snapshot(filepath: str) -> Optional[str]:
    """Return full journal file text for undo snapshots."""
    try:
        return Path(filepath).read_text(encoding="utf-8")
    except OSError:
        return None


def restore_journal_snapshot(filepath: str, snapshot: str) -> bool:
    """Restore full journal file text from an undo snapshot."""
    try:
        write_journal(filepath, snapshot)
        return True
    except OSError:
        return False
        return True
    except OSError:
        return False


def lint_journal(filepath: str) -> List[str]:
    """Validate journal structure and return human-readable lint findings."""
    findings: List[str] = []

    try:
        lines = _read_lines(filepath)
    except Exception:
        return [f"Could not read journal: {filepath}"]

    has_parent_in_section = False

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("##"):
            if parse_date(line) is None:
                findings.append(f"Line {idx}: invalid date header format (expected ## dd/mm/yyyy).")
            has_parent_in_section = False
            continue

        if stripped.startswith(":"):
            if not has_parent_in_section:
                findings.append(f"Line {idx}: note without parent task.")
            continue

        if stripped.startswith("+"):
            if not has_parent_in_section:
                findings.append(f"Line {idx}: subtask without parent task.")
                continue
            subtask = parse_subtask_line(line)
            if subtask is None:
                findings.append(f"Line {idx}: invalid subtask format.")
            continue

        if stripped.startswith("-") and not stripped.startswith("--"):
            has_parent_in_section = True
            task = parse_task_line(line)
            if task is None or not task.title:
                findings.append(f"Line {idx}: invalid task format.")
                continue

            if task.state not in VALID_STATES:
                findings.append(f"Line {idx}: invalid state '{task.state}'.")

            due_meta = re.findall(r"(?:^|\s)--\s*(?:due|d)\s*[:=]\s*([^\s]+)", line, flags=re.IGNORECASE)
            for raw_due in due_meta:
                if _parse_due_value(raw_due) is None:
                    findings.append(f"Line {idx}: invalid due date '{raw_due}' (use dd/mm/yyyy).")

            priority_meta = re.findall(r"(?:^|\s)--\s*(?:priority|prio|p)\s*[:=]\s*([^\s]+)", line, flags=re.IGNORECASE)
            for raw_priority in priority_meta:
                if _parse_priority_value(raw_priority) is None:
                    findings.append(f"Line {idx}: invalid priority '{raw_priority}'.")
            continue

        findings.append(f"Line {idx}: unrecognized line format.")

    return findings
