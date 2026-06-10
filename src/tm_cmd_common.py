"""Shared dataclasses, types, and utility functions for command handlers."""

import re
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .tm_config import DEFAULT_STATE, FINISHED_STATES, VALID_PRIORITIES, VALID_RECURRENCES, RECURRENCE_ALIASES
from .tm_email import EmailConfig
from .tm_features import (
    extract_time_spent_from_line,
    format_time_spent,
    sort_tasks,
    update_time_in_line,
)
from .tm_journal import (
    read_journal_snapshot,
    restore_journal_snapshot,
    update_task_state_in_file,
    write_journal,
)
from .tm_logic import (
    find_task_by_id,
    normalize_priority_input,
    normalize_state_input,
    parse_date_input,
)
from .tm_models import Task, Subtask, extract_tags_from_text
from .tm_log import log as _log
from .tm_ui import Colors, clear_screen, display_tasks

_TAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_-]+")


# ─── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class ViewState:
    """Current task-list filter state."""

    show_done: bool = False
    only_in_progress: bool = False
    only_testing: bool = False
    search_query: Optional[str] = None
    sort_by: str = "none"
    sort_direction: str = "asc"


@dataclass
class CommandContext:
    """Dependencies needed by command handlers."""

    journal_path: str
    email_config: EmailConfig
    refresh_tasks: Callable[[], dict]
    undo_stack: list[str]
    max_undo: int = 20


@dataclass
class CommandOutcome:
    """Result returned by command execution."""

    tasks_by_date: dict
    view_state: ViewState
    should_exit: bool = False
    skip_redraw: bool = False  # True = command printed its own output, don't clear
    new_journal_path: Optional[str] = None  # Signal main loop to switch journal


# ─── Rendering ─────────────────────────────────────────────────────────────

def _render(tasks_by_date: dict, view_state: ViewState) -> None:
    """Render tasks using the current view state, applying sort if configured."""
    if view_state.sort_by != "none":
        sorted_dict = {}
        for date, tasks in tasks_by_date.items():
            sorted_dict[date] = sort_tasks(list(tasks), view_state.sort_by, view_state.sort_direction)
        display_tasks(
            sorted_dict,
            view_state.show_done,
            view_state.only_in_progress,
            view_state.only_testing,
            view_state.search_query,
        )
    else:
        display_tasks(
            tasks_by_date,
            view_state.show_done,
            view_state.only_in_progress,
            view_state.only_testing,
            view_state.search_query,
        )


def _get_state_color_inline(state: str) -> str:
    """Get color code for inline state display."""
    from .tm_ui import get_state_color
    return get_state_color(state)


def _refresh_and_render(context: CommandContext, view_state: ViewState) -> dict:
    """Refresh tasks and repaint the current view."""
    tasks_by_date = context.refresh_tasks()
    clear_screen()
    _render(tasks_by_date, view_state)
    return tasks_by_date


# ─── Utility functions ─────────────────────────────────────────────────────

def _strip_tags(title: str) -> str:
    """Remove hashtag tokens from a title for use in metadata references."""
    return " ".join(_TAG_RE.sub("", title).split())


def _title_without_tags_cmd(text: str) -> str:
    """Remove hashtag tokens from text for display (alias for _strip_tags)."""
    return _strip_tags(text)


def _try_parse_date(raw: str) -> Optional[datetime]:
    """Parse a dd/mm/yyyy date string safely."""
    return parse_date_input(raw)


def _confirm_action(message: str) -> bool:
    """Ask user confirmation for destructive operations."""
    answer = input(f"{Colors.BOLD}{message} [y/N]: {Colors.RESET}").strip().lower()
    return answer in {"y", "yes"}


def _save_undo_snapshot(context: CommandContext, snapshot: Optional[str]) -> None:
    """Push a snapshot onto undo stack, respecting max depth."""
    if snapshot is None:
        return
    context.undo_stack.append(snapshot)
    overflow = len(context.undo_stack) - context.max_undo
    if overflow > 0:
        del context.undo_stack[:overflow]


def _default_archive_path(journal_path: str) -> str:
    """Return the default archive path next to the current journal."""
    journal = Path(journal_path)
    return str(journal.with_name(f"{journal.stem}_archive{journal.suffix}"))


def _maybe_autoclose_parent(context: CommandContext, parent_id: str, view_state: ViewState) -> Optional[dict]:
    """Set parent task to DONE when all subtasks are finished."""
    refreshed = context.refresh_tasks()
    parent = find_task_by_id(refreshed, parent_id)
    if not isinstance(parent, Task):
        return None

    if not parent.subtasks:
        return None

    if parent.is_finished() or not all(subtask.is_finished() for subtask in parent.subtasks):
        return None

    snapshot = read_journal_snapshot(context.journal_path)
    if update_task_state_in_file(context.journal_path, parent, FINISHED_STATES[0]):
        _save_undo_snapshot(context, snapshot)
        latest = context.refresh_tasks()
        clear_screen()
        _log("info", f"All subtasks are DONE. Parent task {parent_id} closed automatically.")
        _render(latest, view_state)
        return latest
    return None


# ─── Metadata parsing ──────────────────────────────────────────────────────

def _strip_inline_tags(text: str) -> str:
    """Remove hashtag tokens and normalize internal spacing."""
    return " ".join(re.sub(r"(?<!\w)#[A-Za-z0-9_-]+", "", text or "").split())


def _apply_tags_to_text(text: str, tags: list[str]) -> str:
    """Replace tags in a text while preserving non-tag content."""
    base_text = _strip_inline_tags(text)
    suffix = " ".join(f"#{tag}" for tag in tags)
    if base_text and suffix:
        return f"{base_text} {suffix}"
    if suffix:
        return suffix
    return base_text


def _extract_inline_meta(text: str) -> tuple[str, list[str], Optional[datetime], Optional[str]]:
    """Extract base text plus inline tags/due/priority from a free text field."""
    tags = extract_tags_from_text(text)

    due_matches = re.findall(r"\[\s*due\s*=\s*(\d{1,2}/\d{1,2}/\d{4})\s*\]", text or "", flags=re.IGNORECASE)
    due_date: Optional[datetime] = None
    if due_matches:
        due_date = parse_date_input(due_matches[-1])

    priority_matches = re.findall(r"\[\s*priority\s*=\s*([A-Za-z]+)\s*\]", text or "", flags=re.IGNORECASE)
    parsed_priority: Optional[str] = None
    if priority_matches:
        parsed_priority = normalize_priority_input(priority_matches[-1])

    without_due = re.sub(r"\[\s*due\s*=\s*\d{1,2}/\d{1,2}/\d{4}\s*\]", "", text or "", flags=re.IGNORECASE)
    without_meta = re.sub(r"\[\s*priority\s*=\s*[A-Za-z]+\s*\]", "", without_due, flags=re.IGNORECASE)
    base_text = _strip_inline_tags(without_meta)

    return base_text, tags, due_date, parsed_priority


def _render_inline_meta_text(
    base_text: str,
    tags: list[str],
    due_date: Optional[datetime],
    priority: Optional[str],
) -> str:
    """Render text with normalized inline tags and optional due/priority markers."""
    parts: list[str] = []
    if base_text:
        parts.append(base_text)
    parts.extend(f"#{tag}" for tag in tags)
    if due_date is not None:
        parts.append(f"[due={due_date.strftime('%d/%m/%Y')}]")
    if priority:
        parts.append(f"[priority={priority}]")
    return " ".join(parts).strip()


def _parse_meta_command(
    raw_command: str,
) -> tuple[Optional[str], bool, Optional[datetime], bool, Optional[str], bool, Optional[list[str]], Optional[str]]:
    """Parse metadata command and return id, due/priority/tags flags-values, and error."""
    try:
        tokens = shlex.split(raw_command)
    except ValueError as exc:
        return None, False, None, False, None, False, None, f"Invalid command syntax: {exc}"

    if len(tokens) < 2:
        return (
            None,
            False,
            None,
            False,
            None,
            False,
            None,
            "Usage: md <id|id.n|id:n#> [--due dd/mm/yyyy|none] [--priority <level>|none] [--tags <list>|none]",
        )

    task_id = tokens[1]
    due_date: Optional[datetime] = None
    priority: Optional[str] = None
    tags: Optional[list[str]] = None
    has_due = False
    has_priority = False
    has_tags = False

    def _parse_tags(raw_tags: str) -> Optional[list[str]]:
        if raw_tags.lower() == "none":
            return []
        chunks = [chunk for chunk in re.split(r"[,\s]+", raw_tags.strip()) if chunk]
        normalized: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            token = chunk.lstrip("#").strip().lower()
            if not token or not re.fullmatch(r"[a-z0-9_-]+", token):
                return None
            if token not in seen:
                seen.add(token)
                normalized.append(token)
        return normalized if normalized else None

    idx = 2
    while idx < len(tokens):
        token = tokens[idx].lower()
        if token == "--due":
            idx += 1
            if idx >= len(tokens):
                return None, False, None, False, None, False, None, "Missing value for --due"
            has_due = True
            raw_due = tokens[idx]
            if raw_due.lower() != "none":
                due_date = _try_parse_date(raw_due)
                if due_date is None:
                    return None, False, None, False, None, False, None, f"Invalid due date: {raw_due}"
            idx += 1
            continue

        if token in ("--priority", "-p"):
            idx += 1
            if idx >= len(tokens):
                return None, False, None, False, None, False, None, "Missing value for --priority"
            has_priority = True
            raw_priority = tokens[idx]
            if raw_priority.lower() != "none":
                priority = normalize_priority_input(raw_priority)
                if priority is None:
                    valid = ", ".join(p.lower() for p in VALID_PRIORITIES)
                    return (
                        None,
                        False,
                        None,
                        False,
                        None,
                        False,
                        None,
                        f"Invalid priority: {raw_priority}. Valid priorities: {valid}.",
                    )
            idx += 1
            continue

        if token in ("--tags", "-t"):
            idx += 1
            if idx >= len(tokens):
                return None, False, None, False, None, False, None, "Missing value for --tags"
            has_tags = True
            parsed_tags = _parse_tags(tokens[idx])
            if parsed_tags is None:
                return (
                    None,
                    False,
                    None,
                    False,
                    None,
                    False,
                    None,
                    "Invalid tags. Use comma/space-separated tokens like backend,qr or 'none'.",
                )
            tags = parsed_tags
            idx += 1
            continue

        return None, False, None, False, None, False, None, f"Unknown option: {tokens[idx]}"

    if not has_due and not has_priority and not has_tags:
        # No flags — return ID with no error so form can be shown
        return task_id, False, None, False, None, False, None, None

    if has_tags and tags is None:
        return None, False, None, False, None, False, None, "Invalid tags. Use comma/space-separated tokens or 'none'."

    return task_id, has_due, due_date, has_priority, priority, has_tags, tags, None


# ─── Time logging ──────────────────────────────────────────────────────────

def _log_time_to_task(context: CommandContext, task: "Task", minutes: int) -> bool:
    """Write spent time to the journal for a task. Returns True on success."""
    if task.source_line is None:
        return False

    from .tm_journal import file_lock
    with file_lock:
        lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
        line_index = task.source_line - 1
        if line_index >= len(lines):
            return False

        task_line = lines[line_index]

        # Calculate existing time: check inline first, then continuation lines
        existing = extract_time_spent_from_line(task_line)
        if existing is None:
            for j in range(line_index + 1, len(lines)):
                cline = lines[j]
                if not cline or not cline[0].isspace():
                    break
                if re.search(r"--\s*(?:spent|time)\s*[:=]\s*(\S+)", cline, re.IGNORECASE):
                    existing = extract_time_spent_from_line(cline)
                    lines.pop(j)
                    break

        total = (existing or 0) + minutes

        # Determine if task uses multiline metadata
        uses_multiline = False
        if line_index + 1 < len(lines):
            next_line = lines[line_index + 1]
            if re.match(r"^\s+--\s*", next_line) or re.match(r"^\s+#", next_line):
                uses_multiline = True

        if uses_multiline:
            indent = "    "
            spent_line = f"{indent}-- spent:{format_time_spent(total)}"
            insert_at = line_index + 1
            for j in range(line_index + 1, len(lines)):
                cline = lines[j]
                if re.match(r"^\s+--\s", cline) or re.match(r"^\s+#", cline):
                    insert_at = j + 1
                else:
                    break
            lines.insert(insert_at, spent_line)
        else:
            lines[line_index] = update_time_in_line(task_line, total)

        write_journal(context.journal_path, "\n".join(lines))
        return True
