"""Journal parsing — extract tasks and metadata from journal files."""

import re
from collections import OrderedDict
from datetime import datetime
from typing import List, Optional, Tuple

from .tm_config import DEFAULT_STATE, PRIORITY_ALIASES, RECURRENCE_ALIASES, STATE_ALIASES, VALID_PRIORITIES, VALID_RECURRENCES, VALID_STATES
from .tm_models import Subtask, Task


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
        pass
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%y")
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

    jira_match = re.match(r"^jira\s*[:=]\s*([A-Z][A-Z0-9]+-\d+)$", chunk, re.IGNORECASE)
    if jira_match:
        task.jira_key = jira_match.group(1).strip().upper()
        return True

    notes_match = re.match(r"^notes\s*[:=]\s*(.+)$", chunk, re.IGNORECASE)
    if notes_match:
        raw = notes_match.group(1).strip()
        if raw:
            task.linked_notes = [n.strip() for n in raw.split(",") if n.strip()]
        return True

    return False


def _apply_subtask_metadata(subtask, chunk: str) -> bool:
    """Apply metadata key:value to a subtask (supports due, priority, notes)."""
    due_match = re.match(r"^(?:due|d)\s*[:=]\s*(\d{1,2}/\d{1,2}/\d{4})$", chunk, re.IGNORECASE)
    if due_match:
        due_date = _parse_due_value(due_match.group(1))
        if due_date is not None:
            subtask.due_date = due_date
            return True
        return False
    prio_match = re.match(r"^(?:priority|prio)\s*[:=]\s*(.+)$", chunk, re.IGNORECASE)
    if prio_match:
        subtask.priority = prio_match.group(1).strip().upper()
        return True
    notes_match = re.match(r"^notes\s*[:=]\s*(.+)$", chunk, re.IGNORECASE)
    if notes_match:
        raw = notes_match.group(1).strip()
        if raw:
            subtask.linked_notes = [n.strip() for n in raw.split(",") if n.strip()]
        else:
            subtask.linked_notes = []
        return True
    return False


def _render_task_line(
    title: str,
    state: str,
    due_date: Optional[datetime],
    priority: Optional[str],
    indent: str = "",
    recurrence: Optional[str] = None,
    jira_key: Optional[str] = None,
    linked_notes: Optional[list] = None,
) -> str:
    parts = [f"{indent}- {title} -- {state}"]
    if due_date is not None:
        parts.append(f"due:{due_date.strftime('%d/%m/%Y')}")
    if priority:
        parts.append(f"priority:{priority}")
    if recurrence:
        parts.append(f"recur:{recurrence}")
    if jira_key:
        parts.append(f"jira:{jira_key}")
    if linked_notes:
        parts.append(f"notes:{','.join(linked_notes)}")
    return " -- ".join(parts) + "\n"


def _render_subtask_line(
    title: str,
    state: str,
    due_date: Optional[datetime] = None,
    priority: Optional[str] = None,
    indent: str = "    ",
    notes: Optional[str] = None,
) -> str:
    """Render a subtask line: + title -- STATE -- due:... -- priority:..."""
    parts = [f"{indent}+ {title} -- {state}"]
    if due_date is not None:
        parts.append(f"due:{due_date.strftime('%d/%m/%Y')}")
    if priority:
        parts.append(f"priority:{priority}")
    if notes:
        parts.append(f"notes:{notes}")
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

        due_match = re.match(r"^(?:due|d)\s*[:=]\s*(\d{1,2}/\d{1,2}/\d{4})$", part, re.IGNORECASE)
        if due_match:
            subtask.due_date = _parse_due_value(due_match.group(1))
            continue

        prio_match = re.match(r"^(?:priority|prio|p)\s*[:=]\s*([A-Za-z]+)$", part, re.IGNORECASE)
        if prio_match:
            prio_val = _parse_priority_value(prio_match.group(1))
            if prio_val is not None:
                subtask.priority = prio_val
            continue

        notes_match = re.match(r"^notes\s*[:=]\s*(.+)$", part, re.IGNORECASE)
        if notes_match:
            raw = notes_match.group(1).strip()
            if raw:
                subtask.linked_notes = [n.strip() for n in raw.split(",") if n.strip()]
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
    """Parse a date line in format '## dd/mm/yyyy' or '## dd/mm/yy'."""
    match = re.match(r"^##\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$", line.strip())
    if match:
        date_str = match.group(1)
        try:
            return datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            pass
        try:
            return datetime.strptime(date_str, "%d/%m/%y")
        except ValueError:
            return None
    return None


def parse_journal(filepath: str) -> dict:
    """Parse the journal file and extract all tasks grouped by date."""
    tasks_by_date = {}
    current_date = None
    last_task: Optional[Task] = None
    last_subtask: Optional[Subtask] = None
    last_element = None

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

                if stripped.startswith("--") and not stripped.startswith("---"):
                    meta_content = stripped[2:].strip()
                    if meta_content:
                        target = last_subtask if last_subtask is not None else last_task
                        if target is not None:
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

                if stripped and last_task is not None:
                    if last_element == 'note' and last_task.comments:
                        last_task.comments[-1] += "\n" + stripped
                    elif last_element == 'subnote' and last_subtask and last_subtask.comments:
                        last_subtask.comments[-1] += "\n" + stripped
                    elif last_element == 'subtask' and last_subtask is not None:
                        last_subtask.title = last_subtask.title.rstrip() + " " + stripped
                    else:
                        last_task.title = last_task.title.rstrip() + " " + stripped

    except FileNotFoundError as exc:
        raise JournalFileNotFoundError(f"File not found: {filepath}") from exc
    except Exception as exc:
        raise JournalReadError(f"Error reading file: {exc}") from exc

    return tasks_by_date


# ─── Block & layout helpers ──────────────────────────────────────────────────


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
            jira_key=task.jira_key,
            linked_notes=task.linked_notes,
        )
    ]
    for comment in task.comments:
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
