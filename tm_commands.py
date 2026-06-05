"""Command dispatch and use-case handlers for the Task Manager CLI."""

import re
import shlex
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from tm_config import DEFAULT_STATE, FINISHED_STATES, VALID_PRIORITIES, VALID_RECURRENCES, RECURRENCE_ALIASES
from tm_email import EmailConfig, EmailResult, send_email_report
from tm_features import (
    compute_next_recurrence_date,
    export_to_csv,
    export_to_json,
    export_to_markdown,
    generate_burndown,
    generate_weekly_report,
    get_all_tags,
    get_tasks_by_tag,
    get_template,
    get_templates,
    import_from_json,
    render_kanban,
    run_pomodoro,
    save_template,
    delete_template,
    sort_tasks,
    parse_time_spent,
    format_time_spent,
    extract_time_spent_from_line,
    update_time_in_line,
    add_blocker_metadata,
    add_blocks_metadata,
)
from tm_journal import (
    add_note_to_task_in_file,
    add_subtask_to_task,
    add_task_to_file,
    archive_finished_tasks_in_file,
    delete_note_in_file,
    delete_subtask_in_file,
    delete_task_in_file,
    duplicate_task_in_file,
    edit_note_in_file,
    edit_subtask_title_in_file,
    edit_task_title_in_file,
    lint_journal,
    mark_all_subtasks_done_in_file,
    move_task_to_date_in_file,
    read_journal_snapshot,
    restore_journal_snapshot,
    update_task_metadata_in_file,
    update_subtask_state_in_file,
    update_task_state_in_file,
    _notify_post_write,
)
from tm_logic import (
    build_pending_email_body,
    find_note_by_id,
    find_task_by_id,
    get_pending_tasks,
    normalize_priority_input,
    normalize_state_input,
    parse_date_input,
    parse_new_command_args,
)
from tm_models import Subtask, Task, extract_tags_from_text
from tm_log import log as _log
from tm_settings import get_setting
from tm_ui import Colors, clear_screen, display_stats, display_tasks, print_help, prompt_for_state

_TAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_-]+")


def _strip_tags(title: str) -> str:
    """Remove hashtag tokens from a title for use in metadata references."""
    return " ".join(_TAG_RE.sub("", title).split())


def _title_without_tags_cmd(text: str) -> str:
    """Remove hashtag tokens from text for display (alias for _strip_tags)."""
    return _strip_tags(text)


COMMAND_HELP = {
    "n": {
        "syntax": "n <title> [--state <state>] [--date dd/mm/yyyy] [--due dd/mm/yyyy] [--priority <level>]",
        "description": "Create a new parent task.",
        "examples": [
            "n Prepare release notes --state backlog --due 10/06/2026 --priority high",
            "n Follow up customer issue --date 04/06/2026",
        ],
    },
    "cs": {
        "syntax": "cs <id> [state]",
        "description": "Change the state of a task or subtask.",
        "examples": ["cs 3 DONE", "cs 4.1"],
    },
    "an": {
        "syntax": "an <id> <note>",
        "description": "Add a note to a parent task.",
        "examples": ["an 3 Review blocker with #backend"],
    },
    "e": {
        "syntax": "e <id> [text] [--due x] [--priority x] [--tags x]",
        "description": "Edit task (form if no args, inline with text or metadata flags). Also: md/meta.",
        "examples": ["e 3", "e 3 New title", "e 3 --due 10/06/2026 --priority high", "e 3 --tags backend,qr"],
    },
    "del": {
        "syntax": "del <id|id:n#>",
        "description": "Delete task, subtask, or note (asks confirmation).",
        "examples": ["del 3", "del 3:n2"],
    },
    "mv": {
        "syntax": "mv <id> <dd/mm/yyyy>",
        "description": "Move a parent task to another date section (asks confirmation).",
        "examples": ["mv 3 10/06/2026"],
    },
    "dup": {
        "syntax": "dup <id> [dd/mm/yyyy]",
        "description": "Duplicate a parent task with notes and subtasks.",
        "examples": ["dup 3", "dup 3 12/06/2026"],
    },
    "sub": {
        "syntax": "sub <id> <title>",
        "description": "Add a subtask to a parent task.",
        "examples": ["sub 3 Review the document", "sub 1 Call supplier"],
    },
    "das": {
        "syntax": "das <id>",
        "description": "Mark all subtasks as DONE and auto-close parent when applicable.",
        "examples": ["das 3"],
    },
    "ar": {
        "syntax": "ar [dd/mm/yyyy]",
        "description": "Archive finished tasks up to optional date (asks confirmation).",
        "examples": ["ar", "ar 10/06/2026"],
    },
    "md": {
        "syntax": "md <id> [--due x] [--priority x] [--tags x]",
        "description": "Alias for 'e' — edit metadata (due/priority/tags).",
        "examples": ["md 3 --due 10/06/2026", "md 3.1 --priority high --tags qa"],
    },
    "ag": {
        "syntax": "ag [days]",
        "description": "Show due-date agenda for next N days (default 7).",
        "examples": ["ag", "ag 14"],
    },
    "day": {
        "syntax": "day [date]",
        "description": "Show tasks created on a date (default: today). Accepts natural dates.",
        "examples": ["day", "hoy", "day 03/06/2026", "day yesterday", "day friday"],
    },
    "ck": {
        "syntax": "ck",
        "description": "Run journal linter and show format/metadata issues.",
        "examples": ["ck"],
    },
    "u": {
        "syntax": "u",
        "description": "Undo last mutation in current session.",
        "examples": ["u"],
    },
    "f": {
        "syntax": "f <text|#tag|priority:...|due:...>",
        "description": "Filter visible tasks by query.",
        "examples": [
            "f #backend",
            "f priority:high",
            "f due:overdue",
            "f due:today",
            "f due:10/06/2026",
            "fc",
        ],
    },
    "fc": {
        "syntax": "fc",
        "description": "Clear current active filter.",
        "examples": ["fc"],
    },
    "se": {
        "syntax": "se [recipient]",
        "description": "Send pending tasks by email.",
        "examples": ["se", "se team@example.com"],
    },
    "kb": {
        "syntax": "kb",
        "description": "Show kanban board view.",
        "examples": ["kb"],
    },
    "pj": {
        "syntax": "pj [#tag]",
        "description": "Show project/tag view. Without argument lists all tags.",
        "examples": ["pj", "pj #backend", "pj backend"],
    },
    "export": {
        "syntax": "export <json|csv|md> [filepath]",
        "description": "Export tasks to file. Default saves next to journal.",
        "examples": ["export json", "export csv /tmp/tasks.csv", "export md"],
    },
    "import": {
        "syntax": "import <filepath>",
        "description": "Import tasks from JSON file.",
        "examples": ["import tasks.json"],
    },
    "wr": {
        "syntax": "wr [days]",
        "description": "Show weekly report (default: last 7 days).",
        "examples": ["wr", "wr 14"],
    },
    "sort": {
        "syntax": "sort <priority|due_date|state|none> [asc|desc]",
        "description": "Set task sort order for display.",
        "examples": ["sort priority", "sort due_date desc", "sort none"],
    },
    "tpl": {
        "syntax": "tpl [name] | tpl save <name> | tpl del <name>",
        "description": "Use, list, save, or delete task templates.",
        "examples": ["tpl", "tpl standup", "tpl save standup", "tpl del standup"],
    },
    "tt": {
        "syntax": "tt <id> <time> | tt <id> start | tt <id> stop",
        "description": "Log time spent on a task (e.g. 2h, 30m, 1h30m).",
        "examples": ["tt 3 1h30m", "tt 3 start", "tt 3 stop"],
    },
    "block": {
        "syntax": "block <id> <id> | block del <blocked_id> <blocker_id>",
        "description": "Mark first task as blocked by second, or remove a specific blocker.",
        "examples": ["block 3 5", "block del 3 5"],
    },
    "unblock": {
        "syntax": "unblock <id>",
        "description": "Remove ALL blockers from a task.",
        "examples": ["unblock 3"],
    },
    "pom": {
        "syntax": "pom [id] [minutes]",
        "description": "Start a pomodoro timer (default 25min). Logs time to task on completion.",
        "examples": ["pom", "pom 3", "pom 3 45"],
    },
    "bd": {
        "syntax": "bd [days]",
        "description": "Show burndown chart (default 14 days).",
        "examples": ["bd", "bd 7", "bd 30"],
    },
}


ALIAS_TO_HELP_KEY = {
    "new": "n",
    "change": "cs",
    "add": "an",
    "edit": "e",
    "delete": "del",
    "move": "mv",
    "reschedule": "mv",
    "duplicate": "dup",
    "done": "das",
    "archive": "ar",
    "meta": "e",
    "agenda": "ag",
    "today": "day",
    "hoy": "day",
    "check": "ck",
    "undo": "u",
    "find": "f",
    "send": "se",
    "kanban": "kb",
    "project": "pj",
    "weekly": "wr",
    "template": "tpl",
    "time": "tt",
    "blocker": "block",
    "pomodoro": "pom",
    "burndown": "bd",
}


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
    from tm_ui import get_state_color
    return get_state_color(state)


def _refresh_and_render(context: CommandContext, view_state: ViewState) -> dict:
    """Refresh tasks and repaint the current view."""
    tasks_by_date = context.refresh_tasks()
    clear_screen()
    _render(tasks_by_date, view_state)
    return tasks_by_date


def _print_email_result(result: EmailResult) -> None:
    """Print a user-facing message based on email dispatch status."""
    if result.status == "sent":
        _log("info", f"{result.message}")
    elif result.status == "draft":
        _log("info", f"{result.message}")
    else:
        _log("error", f"{result.message}")


def _default_archive_path(journal_path: str) -> str:
    """Return the default archive path next to the current journal."""
    journal = Path(journal_path)
    return str(journal.with_name(f"{journal.stem}_archive{journal.suffix}"))


def _try_parse_date(raw: str) -> Optional[datetime]:
    """Parse a dd/mm/yyyy date string safely."""
    return parse_date_input(raw)


def _save_undo_snapshot(context: CommandContext, snapshot: Optional[str]) -> None:
    """Push a snapshot onto undo stack, respecting max depth."""
    if snapshot is None:
        return
    context.undo_stack.append(snapshot)
    overflow = len(context.undo_stack) - context.max_undo
    if overflow > 0:
        del context.undo_stack[:overflow]


def _log_time_to_task(context: CommandContext, task: "Task", minutes: int) -> bool:
    """Write spent time to the journal for a task. Returns True on success.

    Uses task.source_line for reliable location. Handles both inline and
    multiline metadata formats (cross-platform compatible).
    """
    if task.source_line is None:
        return False

    lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
    line_index = task.source_line - 1
    if line_index >= len(lines):
        return False

    task_line = lines[line_index]

    # Calculate existing time: check inline first, then continuation lines
    existing = extract_time_spent_from_line(task_line)
    if existing is None:
        # Check continuation lines for spent:
        for j in range(line_index + 1, len(lines)):
            cline = lines[j]
            if not cline or not cline[0].isspace():
                break
            if re.search(r"--\s*(?:spent|time)\s*[:=]\s*(\S+)", cline, re.IGNORECASE):
                existing = extract_time_spent_from_line(cline)
                # Remove old continuation spent line, we'll re-add updated
                lines.pop(j)
                break

    total = (existing or 0) + minutes

    # Determine if task uses multiline metadata (next line is indented --)
    uses_multiline = False
    if line_index + 1 < len(lines):
        next_line = lines[line_index + 1]
        if re.match(r"^\s+--\s*", next_line) or re.match(r"^\s+#", next_line):
            uses_multiline = True

    if uses_multiline:
        # Insert as continuation line after the task line (before other content)
        indent = "    "
        from tm_features import format_time_spent
        spent_line = f"{indent}-- spent:{format_time_spent(total)}"
        # Find insertion point: after last -- line
        insert_at = line_index + 1
        for j in range(line_index + 1, len(lines)):
            cline = lines[j]
            if re.match(r"^\s+--\s", cline) or re.match(r"^\s+#", cline):
                insert_at = j + 1
            else:
                break
        lines.insert(insert_at, spent_line)
    else:
        # Inline: append or update on task line
        lines[line_index] = update_time_in_line(task_line, total)

    Path(context.journal_path).write_text("\n".join(lines), encoding="utf-8")
    _notify_post_write()
    return True


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


def _print_agenda(tasks_by_date: dict, days_ahead: int = 7) -> None:
    """Print due-date agenda grouped by urgency."""
    today = datetime.now().date()
    week_limit = today + timedelta(days=days_ahead)

    overdue: list[Task] = []
    due_today: list[Task] = []
    due_soon: list[Task] = []

    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.is_finished() or task.due_date is None:
                continue
            due = task.due_date.date()
            if due < today:
                overdue.append(task)
            elif due == today:
                due_today.append(task)
            elif due <= week_limit:
                due_soon.append(task)

    def _print_group(title: str, items: list[Task], icon: str = "") -> None:
        print(f"\n  {Colors.BOLD}{icon}{title}{Colors.RESET}")
        if not items:
            print(f"    {Colors.DIM}(none){Colors.RESET}")
            return
        ordered = sorted(items, key=lambda item: item.due_date or datetime.max)
        for task in ordered:
            task_id = task.task_id or "?"
            state_color = _get_state_color_inline(task.state)
            due_str = task.due_date.strftime("%d/%m/%Y") if task.due_date else ""
            priority_badge = f" [P:{task.priority}]" if task.priority else ""
            title_clean = task.title
            print(
                f"    [{task_id}] {state_color}{task.state:<{11}}{Colors.RESET} "
                f"{title_clean}{Colors.DIM}{priority_badge} [DUE:{due_str}]{Colors.RESET}"
            )

    tw = shutil.get_terminal_size((80, 24)).columns
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'─' * 3} Agenda {'─' * (tw - 12)}{Colors.RESET}")
    _print_group("Overdue", overdue, "⚠ ")
    _print_group("Due Today", due_today, "◉ ")
    _print_group(f"Due Next {days_ahead} Days", due_soon, "◌ ")


def _confirm_action(message: str) -> bool:
    """Ask user confirmation for destructive operations."""
    answer = input(f"{Colors.BOLD}{message} [y/N]: {Colors.RESET}").strip().lower()
    return answer in {"y", "yes"}


def _resolve_help_key(raw_command_name: str) -> Optional[str]:
    """Resolve aliases to canonical command key used in COMMAND_HELP."""
    command = raw_command_name.strip().lower()
    if command in COMMAND_HELP:
        return command
    return ALIAS_TO_HELP_KEY.get(command)


def _extract_help_request(raw_command: str) -> Optional[str]:
    """Return command name if user requested inline command help with -h/--help."""
    try:
        tokens = shlex.split(raw_command)
    except ValueError:
        return None

    if len(tokens) != 2:
        return None

    if tokens[1] not in ("-h", "--help"):
        return None

    return tokens[0]


def _print_command_help(help_key: str) -> None:
    """Print detailed help for a specific command."""
    info = COMMAND_HELP.get(help_key)
    if not info:
        _log("error", f"No help available for that command.")
        return

    print(f"\n{Colors.HEADER}{Colors.BOLD}Command Help: {help_key}{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 72}{Colors.RESET}")
    print(f"{Colors.BOLD}Syntax:{Colors.RESET} {info['syntax']}")
    print(f"{Colors.BOLD}Description:{Colors.RESET} {info['description']}")
    print(f"{Colors.BOLD}Examples:{Colors.RESET}")
    for example in info["examples"]:
        print(f"  {example}")


def execute_command(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> CommandOutcome:
    """Execute a single user command and return updated state."""
    command = raw_command.lower()

    requested_help = _extract_help_request(raw_command)
    if requested_help:
        help_key = _resolve_help_key(requested_help)
        if help_key:
            _print_command_help(help_key)
            return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)

    if command == "fc":
        next_view = ViewState(
            show_done=view_state.show_done,
            only_in_progress=view_state.only_in_progress,
            only_testing=view_state.only_testing,
            search_query=None,
            sort_by=view_state.sort_by,
            sort_direction=view_state.sort_direction,
        )
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("u", "undo"):
        if not context.undo_stack:
            _log("info", f"Nothing to undo.")
            return CommandOutcome(tasks_by_date, view_state)

        snapshot = context.undo_stack.pop()
        if restore_journal_snapshot(context.journal_path, snapshot):
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Undid last change.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not restore undo snapshot.")
        return CommandOutcome(tasks_by_date, view_state)

    if re.match(r"^\s*(?:ag|agenda)(?:\s+\d+)?\s*$", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*(?:ag|agenda)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
        days = int(match.group(1)) if match and match.group(1) else 7
        if days < 1 or days > 90:
            _log("error", f"Agenda days must be between 1 and 90.")
            return CommandOutcome(tasks_by_date, view_state)
        refreshed = context.refresh_tasks()
        _print_agenda(refreshed, days_ahead=days)
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # ─── Day view: show tasks created on a specific date ─────────────
    if re.match(r"^\s*(?:day|hoy|today)(?:\s+.+)?\s*$", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*(?:day|hoy|today)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        date_arg = match.group(1).strip() if match and match.group(1) else None
        if date_arg:
            target = parse_date_input(date_arg)
            if target is None:
                _log("error", f"Invalid date: {date_arg}")
                return CommandOutcome(tasks_by_date, view_state)
        else:
            target = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        refreshed = context.refresh_tasks()
        filtered = {}
        for d, tl in refreshed.items():
            if d and d.date() == target.date():
                filtered[d] = tl
        if not filtered:
            _log("info", f"No tasks for {target.strftime('%d/%m/%Y')}.")
        else:
            display_tasks(filtered, show_done=view_state.show_done)
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    if command in ("ck", "check"):
        findings = lint_journal(context.journal_path)
        if findings:
            print(f"\n{Colors.ERROR}{Colors.BOLD}Journal check found issues:{Colors.RESET}")
            for finding in findings:
                print(f"  - {finding}")
        else:
            _log("info", f"Journal check passed. No issues found.")
        refreshed = context.refresh_tasks()
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    if command in ("q", "quit", "exit"):
        return CommandOutcome(tasks_by_date, view_state, should_exit=True)

    if command in ("cls", "clear"):
        clear_screen()
        _render(tasks_by_date, view_state)
        return CommandOutcome(tasks_by_date, view_state)

    if command in ("a", "all"):
        next_view = ViewState(show_done=True, search_query=view_state.search_query, sort_by=view_state.sort_by, sort_direction=view_state.sort_direction)
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, next_view)

    if command in ("p", "pending"):
        next_view = ViewState(search_query=view_state.search_query, sort_by=view_state.sort_by, sort_direction=view_state.sort_direction)
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, next_view)

    if command in ("s", "stats"):
        updated_tasks = context.refresh_tasks()
        display_stats(updated_tasks)
        return CommandOutcome(updated_tasks, view_state, skip_redraw=True)

    if re.match(r"^\s*(?:se|send\s+email)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        pending = get_pending_tasks(updated_tasks)
        if not pending:
            _log("info", f"No pending tasks to send.")
            return CommandOutcome(updated_tasks, view_state)

        match = re.match(r"^\s*(?:se|send\s+email)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        recipient = match.group(1).strip() if match and match.group(1) else None
        if not recipient:
            recipient = context.email_config.default_recipient
        while not recipient:
            answer = input(f"{Colors.BOLD}Recipient email: {Colors.RESET}").strip()
            if answer:
                recipient = answer

        subject = f"{context.email_config.subject_prefix} Pending tasks {datetime.now().strftime('%d/%m/%Y')}"
        body = build_pending_email_body(updated_tasks)
        result = send_email_report(recipient, subject, body, context.email_config)
        _print_email_result(result)
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:n|new)\b", raw_command, re.IGNORECASE):
        task_title, task_state, target_date, due_date, priority, recurrence, parse_error = parse_new_command_args(raw_command)
        if parse_error:
            _log("error", f"{parse_error}")
            print(
                f"{Colors.DIM}Usage: n [title] [--state <state>] [--date dd/mm/yyyy] "
                f"[--due dd/mm/yyyy] [--priority <level>] [--recur <freq>]{Colors.RESET}"
            )
            return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)

        result = None  # Will be set if interactive form is used
        if not task_title:
            # Show interactive form
            from tm_form import show_form, TextField, SelectField
            from tm_config import VALID_STATES, VALID_PRIORITIES

            form_fields = [
                TextField("Title", placeholder="Task title (required)"),
                SelectField("State", VALID_STATES, selected=VALID_STATES.index(DEFAULT_STATE) if DEFAULT_STATE in VALID_STATES else 0),
                TextField("Due date", placeholder="dd/mm/yyyy (optional)"),
                SelectField("Priority", VALID_PRIORITIES, allow_empty=True),
                TextField("Tags", placeholder="tag1 tag2 (optional)"),
                TextField("Note", placeholder="Add a note (optional)"),
                TextField("Recurrence", placeholder="daily/weekly/monthly (optional)"),
            ]

            try:
                result = show_form("New Task", form_fields)
            except Exception as exc:
                import traceback
                Path("ttm_crash.log").write_text(traceback.format_exc(), encoding="utf-8")
                _log("error", f"Form crashed. See ttm_crash.log")
                clear_screen()
                _render(tasks_by_date, view_state)
                return CommandOutcome(tasks_by_date, view_state)
            if result is None:
                clear_screen()
                _render(tasks_by_date, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(tasks_by_date, view_state)

            task_title = result["Title"].strip()
            if result.get("Tags", "").strip():
                raw_tags = result["Tags"].strip()
                # Ensure each tag has # prefix
                tags = []
                for t in raw_tags.split():
                    tags.append(t if t.startswith("#") else f"#{t}")
                task_title += " " + " ".join(tags)
            task_state = result.get("State") or DEFAULT_STATE
            if result.get("Due date", "").strip():
                due_date = parse_date_input(result["Due date"].strip())
            if result.get("Priority", "").strip():
                priority = normalize_priority_input(result["Priority"])
            if result.get("Recurrence", "").strip():
                from tm_logic import normalize_recurrence_input
                recurrence = normalize_recurrence_input(result["Recurrence"].strip())

        if not task_title:
            _log("error", f"Task title cannot be empty.")
            return CommandOutcome(tasks_by_date, view_state)

        task_state = task_state or DEFAULT_STATE
        snapshot = read_journal_snapshot(context.journal_path)

        if add_task_to_file(context.journal_path, task_title, task_state, target_date, due_date, priority, recurrence):
            _save_undo_snapshot(context, snapshot)
            # Add note if provided via form
            if result is not None and result.get("Note", "").strip():
                updated_tasks = context.refresh_tasks()
                # Find the just-created task by title match
                created_task = None
                for tasks in updated_tasks.values():
                    for t in tasks:
                        if t.title.strip() == task_title.strip():
                            created_task = t
                if created_task:
                    add_note_to_task_in_file(context.journal_path, created_task, result["Note"].strip())
            updated_tasks = context.refresh_tasks()
            clear_screen()
            created_date = (target_date or datetime.now()).strftime("%d/%m/%Y")
            extra = []
            if due_date:
                extra.append(f"due {due_date.strftime('%d/%m/%Y')}")
            if priority:
                extra.append(f"priority {priority}")
            if recurrence:
                extra.append(f"recur {recurrence}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            _log("info", f"Task created in {task_state} for {created_date}{suffix}.")
            _render(updated_tasks, view_state)
            return CommandOutcome(updated_tasks, view_state)

        _log("error", f"Could not create task in file.")
        return CommandOutcome(tasks_by_date, view_state)

    if re.match(r"^\s*(?:cs|change\s+state)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:cs|change\s+state)\s+(\S+)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: cs <task_id> [state]")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target_task = find_task_by_id(updated_tasks, requested_id)
        if not target_task:
            _log("error", f"Task ID {requested_id} not found.")
            return CommandOutcome(updated_tasks, view_state)

        selected_state = None
        requested_state = match.group(2)
        if requested_state:
            selected_state = normalize_state_input(requested_state)

        if not selected_state:
            if requested_state:
                _log("error", f"Invalid state: {requested_state}")
            from tm_form import show_form, SelectField
            from tm_config import VALID_STATES as _VS
            current_idx = _VS.index(target_task.state) if target_task.state in _VS else 0
            form_fields = [SelectField("State", _VS, selected=current_idx)]
            result = show_form(f"Change State — {_strip_tags(target_task.title)[:30]}", form_fields)
            if result is None:
                clear_screen()
                _render(updated_tasks, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(updated_tasks, view_state)
            selected_state = result["State"]

        parent_id = None
        if isinstance(target_task, Subtask):
            parent_id = requested_id.split(".", 1)[0]
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = update_subtask_state_in_file(context.journal_path, target_task, selected_state)
        else:
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = update_task_state_in_file(context.journal_path, target_task, selected_state)

        if persisted:
            _save_undo_snapshot(context, snapshot)
            # Handle recurring tasks: create next instance on completion
            if (
                not isinstance(target_task, Subtask)
                and selected_state in FINISHED_STATES
                and getattr(target_task, "recurrence", None)
            ):
                base_date = target_task.due_date or target_task.date or datetime.now()
                next_date = compute_next_recurrence_date(base_date, target_task.recurrence)
                next_due = None
                if target_task.due_date:
                    next_due = compute_next_recurrence_date(target_task.due_date, target_task.recurrence)
                add_task_to_file(
                    context.journal_path,
                    target_task.title,
                    DEFAULT_STATE,
                    next_date,
                    next_due,
                    target_task.priority,
                    target_task.recurrence,
                )
                _log("info", f"Recurring task created for {next_date.strftime('%d/%m/%Y')}.")

            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Task {requested_id} updated to {selected_state}.")
            _render(refreshed, view_state)
            if parent_id:
                maybe_closed = _maybe_autoclose_parent(context, parent_id, view_state)
                if maybe_closed is not None:
                    refreshed = maybe_closed
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not update task in file.")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:an|add\s+note)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:an|add\s+note)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
        id_only_match = re.match(r"^\s*(?:an|add\s+note)\s+(\S+)\s*$", raw_command, re.IGNORECASE)

        if not match and not id_only_match:
            _log("error", f"Usage: an <task_id> [note]")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = (match.group(1) if match else id_only_match.group(1)).strip()

        target_task = find_task_by_id(updated_tasks, requested_id)
        if not target_task:
            _log("error", f"Task ID {requested_id} not found.")
            return CommandOutcome(updated_tasks, view_state)

        if isinstance(target_task, Subtask):
            _log("error", f"Add note supports parent task IDs only.")
            return CommandOutcome(updated_tasks, view_state)

        if match:
            note_text = match.group(2).strip()
        else:
            from tm_form import show_form, TextField
            form_fields = [TextField("Note", placeholder="Note text")]
            result = show_form(f"Add Note — {_strip_tags(target_task.title)[:30]}", form_fields)
            if result is None:
                clear_screen()
                _render(updated_tasks, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(updated_tasks, view_state)
            note_text = result["Note"].strip()

        if not note_text:
            _log("error", f"Note cannot be empty.")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if add_note_to_task_in_file(context.journal_path, target_task, note_text):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Note added to task {requested_id}.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not add note in file.")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:e|edit|md|meta)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()

        # ─── Check for metadata flags (--due, --priority, --tags) ─────
        # This replaces the old standalone `md` command.
        has_meta_flags = bool(re.search(r"--(?:due|priority|tags)\b|-[pt]\b", raw_command))
        if has_meta_flags or re.match(r"^\s*(?:md|meta)\b", raw_command, re.IGNORECASE):
            requested_id, has_due, due_date, has_priority, priority, has_tags, tags, parse_error = _parse_meta_command(raw_command)
            if parse_error:
                _log("error", f"{parse_error}")
                return CommandOutcome(updated_tasks, view_state)

            note_target = find_note_by_id(updated_tasks, requested_id or "")
            if note_target is not None:
                _log("error", f"Notes don't support metadata. Use 'e {requested_id} <text>' to edit.")
                return CommandOutcome(updated_tasks, view_state)

            target = find_task_by_id(updated_tasks, requested_id or "")
            if target is None:
                _log("error", f"ID {requested_id} not found.")
                return CommandOutcome(updated_tasks, view_state)

            # If no flags provided, fall through to interactive form below
            if not has_due and not has_priority and not has_tags:
                pass  # will be handled by the form section below
            else:
                # Apply metadata flags directly (inline mode)
                if isinstance(target, Subtask):
                    base_title, existing_tags, existing_due, existing_priority = _extract_inline_meta(target.title)
                    next_tags = tags or [] if has_tags else existing_tags
                    next_due = due_date if has_due else existing_due
                    next_priority = priority if has_priority else existing_priority
                    next_title = _render_inline_meta_text(base_title, next_tags, next_due, next_priority)
                    snapshot = read_journal_snapshot(context.journal_path)
                    if edit_subtask_title_in_file(context.journal_path, target, next_title):
                        _save_undo_snapshot(context, snapshot)
                        refreshed = context.refresh_tasks()
                        clear_screen()
                        _log("info", f"Updated metadata for {requested_id}.")
                        _render(refreshed, view_state)
                        return CommandOutcome(refreshed, view_state)
                    _log("error", f"Could not update subtask metadata in file.")
                    return CommandOutcome(updated_tasks, view_state)

                next_due = due_date if has_due else target.due_date
                next_priority = priority if has_priority else target.priority
                snapshot = read_journal_snapshot(context.journal_path)

                if has_tags:
                    next_title = _apply_tags_to_text(target.title, tags or [])
                    if not edit_task_title_in_file(context.journal_path, target, next_title):
                        _log("error", f"Could not update task tags in file.")
                        return CommandOutcome(updated_tasks, view_state)
                    updated_tasks = context.refresh_tasks()
                    refreshed_target = find_task_by_id(updated_tasks, requested_id or "")
                    if isinstance(refreshed_target, Task):
                        target = refreshed_target
                        next_due = due_date if has_due else target.due_date
                        next_priority = priority if has_priority else target.priority

                if update_task_metadata_in_file(context.journal_path, target, next_due, next_priority):
                    _save_undo_snapshot(context, snapshot)
                    refreshed = context.refresh_tasks()
                    clear_screen()
                    due_label = next_due.strftime("%d/%m/%Y") if next_due else "none"
                    priority_label = next_priority or "none"
                    _log("info", f"Updated metadata for {requested_id}: due={due_label}, priority={priority_label}, tags={'updated' if has_tags else 'unchanged'}.")
                    _render(refreshed, view_state)
                    return CommandOutcome(refreshed, view_state)

                _log("error", f"Could not update metadata in file.")
                return CommandOutcome(updated_tasks, view_state)

        # ─── Interactive form: e <id> (no trailing text, no flags) ────
        match_no_text = re.match(r"^\s*(?:e|edit|md|meta)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        match = re.match(r"^\s*(?:e|edit|md|meta)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)

        if match_no_text and not match:
            requested_id = match_no_text.group(1).strip()
            target = find_task_by_id(updated_tasks, requested_id)
            if target and not isinstance(target, Subtask):
                # Show interactive edit form for parent task
                from tm_form import show_form, TextField, SelectField
                from tm_config import VALID_STATES, VALID_PRIORITIES

                # Strip tags from title for the field, show tags separately
                tags = " ".join(f"#{t}" for t in target.get_tags())
                title_no_tags = _strip_tags(target.title)

                state_idx = VALID_STATES.index(target.state) if target.state in VALID_STATES else 0
                prio_idx = VALID_PRIORITIES.index(target.priority) if target.priority and target.priority in VALID_PRIORITIES else -1

                form_fields = [
                    TextField("Title", value=title_no_tags),
                    SelectField("State", VALID_STATES, selected=state_idx),
                    TextField("Due date", value=target.due_date.strftime("%d/%m/%Y") if target.due_date else ""),
                    SelectField("Priority", VALID_PRIORITIES, selected=prio_idx, allow_empty=True),
                    TextField("Tags", value=tags),
                    TextField("Note", placeholder="Add a note (optional)"),
                ]

                try:
                    result = show_form(f"Edit — {_strip_tags(target.title)[:30]}", form_fields)
                except Exception as exc:
                    import traceback
                    Path("ttm_crash.log").write_text(traceback.format_exc(), encoding="utf-8")
                    clear_screen()
                    _render(updated_tasks, view_state)
                    _log("error", f"Form crashed. See ttm_crash.log")
                    return CommandOutcome(updated_tasks, view_state)
                if result is None:
                    clear_screen()
                    _render(updated_tasks, view_state)
                    _log("info", f"Cancelled.")
                    return CommandOutcome(updated_tasks, view_state)

                new_title = result["Title"].strip()
                if result.get("Tags", "").strip():
                    raw_tags = result["Tags"].strip()
                    new_tags = " ".join(t if t.startswith("#") else f"#{t}" for t in raw_tags.split())
                    new_title += " " + new_tags

                new_state = result.get("State") or target.state
                new_due = parse_date_input(result["Due date"].strip()) if result.get("Due date", "").strip() else None
                new_priority = normalize_priority_input(result["Priority"]) if result.get("Priority", "").strip() else None

                snapshot = read_journal_snapshot(context.journal_path)
                # Mutate the target object with all new values so that
                # every file-write function uses consistent data.
                target.title = new_title if new_title else target.title
                target.due_date = new_due
                target.priority = new_priority
                target.state = new_state
                # Use update_task_state_in_file which: re-reads the file,
                # extracts raw_title from the line, removes stale continuation
                # metadata (-- STATE, -- due:, -- priority:), and renders a
                # single clean line with all values.  Always call it (even if
                # state didn't change) so continuation lines are cleaned up.
                # First write the title (in case it changed):
                edit_task_title_in_file(context.journal_path, target, target.title)
                # Then consolidate state+metadata (cleans continuations):
                update_task_state_in_file(context.journal_path, target, new_state)
                # Add note if provided
                note_text = result.get("Note", "").strip()
                if note_text:
                    add_note_to_task_in_file(context.journal_path, target, note_text)
                _save_undo_snapshot(context, snapshot)
                refreshed = context.refresh_tasks()
                clear_screen()
                _log("info", f"Task {requested_id} updated.")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)
            elif target is None:
                _log("error", f"ID {requested_id} not found.")
                return CommandOutcome(updated_tasks, view_state)
            else:
                # Subtask without text — show usage
                _log("error", f"Usage: e <task_id|subtask_id|task_id:n#> <new text>")
                return CommandOutcome(updated_tasks, view_state)

        # ─── Inline edit: e <id> <new text> ───────────────────────────
        if not match:
            _log("error", f"Usage: e <id> [text] [--due x] [--priority x] [--tags x]")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        new_title = match.group(2).strip()
        if not new_title:
            _log("error", f"New title cannot be empty.")
            return CommandOutcome(updated_tasks, view_state)

        note_target = find_note_by_id(updated_tasks, requested_id)
        if note_target is not None:
            task, note_index, _ = note_target
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = edit_note_in_file(context.journal_path, task, note_index, new_title)
            if persisted:
                _save_undo_snapshot(context, snapshot)
                refreshed = context.refresh_tasks()
                clear_screen()
                _log("info", f"Updated note {requested_id}.")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)

            _log("error", f"Could not edit note in file.")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id)
        if target is None:
            _log("error", f"ID {requested_id} not found.")
            return CommandOutcome(updated_tasks, view_state)

        if isinstance(target, Subtask):
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = edit_subtask_title_in_file(context.journal_path, target, new_title)
        else:
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = edit_task_title_in_file(context.journal_path, target, new_title)

        if persisted:
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Updated title for {requested_id}.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not edit title in file.")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:del|delete)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:del|delete)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: del <task_id|subtask_id|task_id:n#>")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        if not _confirm_action(f"Delete {requested_id}?"):
            _log("info", f"Delete cancelled.")
            return CommandOutcome(updated_tasks, view_state)

        note_target = find_note_by_id(updated_tasks, requested_id)
        if note_target is not None:
            task, note_index, _ = note_target
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = delete_note_in_file(context.journal_path, task, note_index)
            if persisted:
                _save_undo_snapshot(context, snapshot)
                refreshed = context.refresh_tasks()
                clear_screen()
                _log("info", f"Deleted note {requested_id}.")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)
            _log("error", f"Could not delete note in file.")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id)
        if target is None:
            _log("error", f"ID {requested_id} not found.")
            return CommandOutcome(updated_tasks, view_state)

        if isinstance(target, Subtask):
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = delete_subtask_in_file(context.journal_path, target)
        else:
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = delete_task_in_file(context.journal_path, target)

        if persisted:
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Deleted {requested_id}.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not delete item in file.")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:mv|move|reschedule)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:mv|move|reschedule)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
        id_only_match = re.match(r"^\s*(?:mv|move|reschedule)\s+(\S+)\s*$", raw_command, re.IGNORECASE)

        if not match and not id_only_match:
            _log("error", f"Usage: mv <task_id> [date]")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = (match.group(1) if match else id_only_match.group(1)).strip()

        target = find_task_by_id(updated_tasks, requested_id)
        if target is None or isinstance(target, Subtask):
            _log("error", f"Move supports parent task IDs only.")
            return CommandOutcome(updated_tasks, view_state)

        if match:
            date_input = match.group(2).strip()
        else:
            from tm_form import show_form, TextField
            form_fields = [TextField("Date", placeholder="dd/mm/yyyy or tomorrow, monday...")]
            result = show_form(f"Move — {_strip_tags(target.title)[:30]}", form_fields)
            if result is None:
                clear_screen()
                _render(updated_tasks, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(updated_tasks, view_state)
            date_input = result["Date"].strip()

        target_date = parse_date_input(date_input)
        if target_date is None:
            _log("error", f"Invalid date: {date_input}")
            return CommandOutcome(updated_tasks, view_state)

        if not _confirm_action(f"Move task {requested_id} to {target_date.strftime('%d/%m/%Y')}?"):
            _log("info", f"Move cancelled.")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if move_task_to_date_in_file(context.journal_path, target, target_date):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Moved task {requested_id} to {target_date.strftime('%d/%m/%Y')}.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not move task in file.")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:dup|duplicate)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(
            r"^\s*(?:dup|duplicate)\s+(\S+)(?:\s+(\d{1,2}/\d{1,2}/\d{4}))?\s*$",
            raw_command,
            re.IGNORECASE,
        )
        if not match:
            _log("error", f"Usage: dup <task_id> [dd/mm/yyyy]")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target = find_task_by_id(updated_tasks, requested_id)
        if target is None or isinstance(target, Subtask):
            _log("error", f"Duplicate supports parent task IDs only.")
            return CommandOutcome(updated_tasks, view_state)

        target_date = _try_parse_date(match.group(2)) if match.group(2) else None
        if match.group(2) and target_date is None:
            _log("error", f"Invalid date. Use dd/mm/yyyy.")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if duplicate_task_in_file(context.journal_path, target, target_date):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Duplicated task {requested_id}.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not duplicate task in file.")
        return CommandOutcome(updated_tasks, view_state)

    # ─── Add Subtask ───────────────────────────────────────────────────
    if re.match(r"^\s*sub\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*sub\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
        id_only_match = re.match(r"^\s*sub\s+(\S+)\s*$", raw_command, re.IGNORECASE)

        if not match and not id_only_match:
            _log("error", f"Usage: sub <id> [subtask title]")
            return CommandOutcome(refreshed, view_state)

        task_id = match.group(1) if match else id_only_match.group(1)
        target = find_task_by_id(refreshed, task_id)

        if not target or isinstance(target, Subtask):
            _log("error", f"Task {task_id} not found (must be parent task).")
            return CommandOutcome(refreshed, view_state)

        if match:
            sub_title = match.group(2).strip()
            sub_state = DEFAULT_STATE
        else:
            # Show interactive form
            from tm_form import show_form, TextField, SelectField
            from tm_config import VALID_STATES as _VS, VALID_PRIORITIES as _VP
            form_fields = [
                TextField("Title", placeholder="Subtask title (required)"),
                SelectField("State", _VS, selected=_VS.index(DEFAULT_STATE) if DEFAULT_STATE in _VS else 0),
                TextField("Due date", placeholder="dd/mm/yyyy (optional)"),
                SelectField("Priority", _VP, allow_empty=True),
                TextField("Tags", placeholder="tag1 tag2 (optional)"),
            ]
            result = show_form(f"New Subtask — {_strip_tags(target.title)[:30]}", form_fields)
            if result is None:
                clear_screen()
                _render(refreshed, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(refreshed, view_state)
            sub_title = result["Title"].strip()
            if not sub_title:
                clear_screen()
                _render(refreshed, view_state)
                _log("error", f"Subtask title cannot be empty.")
                return CommandOutcome(refreshed, view_state)
            sub_state = result.get("State") or DEFAULT_STATE
            # Build inline metadata
            due_str = result.get("Due date", "").strip()
            prio_str = result.get("Priority", "").strip()
            tags_str = result.get("Tags", "").strip()
            if tags_str:
                sub_title += " " + " ".join(t if t.startswith("#") else f"#{t}" for t in tags_str.split())
            if due_str:
                sub_title += f" [due={due_str}]"
            if prio_str:
                sub_title += f" [priority={prio_str}]"

        snapshot = read_journal_snapshot(context.journal_path)
        if add_subtask_to_task(context.journal_path, target, sub_title, sub_state):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Subtask added to task {task_id}.")
            _render(refreshed, view_state)
        else:
            _log("error", f"Could not add subtask.")

        return CommandOutcome(refreshed, view_state)

    if re.match(r"^\s*(?:das|done\s+all\s+subtasks)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:das|done\s+all\s+subtasks)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: das <task_id>")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target = find_task_by_id(updated_tasks, requested_id)
        if target is None or isinstance(target, Subtask):
            _log("error", f"Done-all-subtasks supports parent task IDs only.")
            return CommandOutcome(updated_tasks, view_state)

        if not target.subtasks:
            _log("error", f"Task {requested_id} has no subtasks.")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if mark_all_subtasks_done_in_file(context.journal_path, target):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"All subtasks in {requested_id} updated to DONE.")
            _render(refreshed, view_state)
            maybe_closed = _maybe_autoclose_parent(context, requested_id, view_state)
            if maybe_closed is not None:
                refreshed = maybe_closed
            return CommandOutcome(refreshed, view_state)

        _log("error", f"Could not update subtasks in file.")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:ar|archive)\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*(?:ar|archive)(?:\s+(\d{1,2}/\d{1,2}/\d{4}))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: ar [dd/mm/yyyy]")
            return CommandOutcome(tasks_by_date, view_state)

        before_date = _try_parse_date(match.group(1)) if match.group(1) else None
        if match.group(1) and before_date is None:
            _log("error", f"Invalid date. Use dd/mm/yyyy.")
            return CommandOutcome(tasks_by_date, view_state)

        date_label = before_date.strftime('%d/%m/%Y') if before_date else 'all dates'
        if not _confirm_action(f"Archive finished tasks up to {date_label}?"):
            _log("info", f"Archive cancelled.")
            return CommandOutcome(tasks_by_date, view_state)

        archive_path = _default_archive_path(context.journal_path)
        snapshot = read_journal_snapshot(context.journal_path)
        moved = archive_finished_tasks_in_file(context.journal_path, archive_path, before_date)
        if moved > 0:
            _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        _log("info", f"Archived {moved} finished task(s) to {archive_path}.")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    if re.match(r"^\s*(?:f|find)\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*(?:f|find)\s*(.*)$", raw_command, re.IGNORECASE)
        query = match.group(1).strip() if match else ""
        if query.lower() in {"", "clear"}:
            next_view = ViewState(
                show_done=view_state.show_done,
                only_in_progress=view_state.only_in_progress,
                only_testing=view_state.only_testing,
                search_query=None,
            )
            updated_tasks = _refresh_and_render(context, next_view)
            return CommandOutcome(updated_tasks, next_view)

        next_view = ViewState(
            show_done=view_state.show_done,
            only_in_progress=view_state.only_in_progress,
            only_testing=view_state.only_testing,
            search_query=query,
        )
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("r", "refresh"):
        refreshed = context.refresh_tasks()
        clear_screen()
        _log("info", f"Refreshed!")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    # ─── Templates ──────────────────────────────────────────────────────
    if re.match(r"^\s*(?:tpl|template)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:tpl|template)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        arg = match.group(1).strip() if match and match.group(1) else None

        if not arg:
            # List templates
            templates = get_templates()
            if not templates:
                _log("info", f"No templates saved. Use 'tpl save <name>' after creating a task to save it as template.")
                return CommandOutcome(refreshed, view_state)
            print(f"\n{Colors.HEADER}{Colors.BOLD}Templates{Colors.RESET}")
            for name, data in templates.items():
                subtask_count = len(data.get("subtasks", []))
                extra = []
                if data.get("state"):
                    extra.append(data["state"])
                if data.get("priority"):
                    extra.append(data["priority"])
                if subtask_count:
                    extra.append(f"{subtask_count} subtasks")
                suffix = f" ({', '.join(extra)})" if extra else ""
                print(f"  {Colors.BOLD}{name}{Colors.RESET}: {data.get('title', '?')}{suffix}")
            return CommandOutcome(refreshed, view_state, skip_redraw=True)

        # tpl save <name> — save last created task as template
        save_match = re.match(r"^save\s+(\S+)$", arg, re.IGNORECASE)
        if save_match:
            tpl_name = save_match.group(1)
            from tm_form import show_form, TextField, SelectField
            from tm_config import VALID_STATES as _VS, VALID_PRIORITIES as _VP
            form_fields = [
                TextField("Title", placeholder="Template title (required)"),
                SelectField("State", _VS, selected=_VS.index(DEFAULT_STATE) if DEFAULT_STATE in _VS else 0),
                SelectField("Priority", _VP, allow_empty=True),
                TextField("Subtasks", placeholder="sub1, sub2, sub3 (comma-separated)"),
            ]
            result = show_form(f"Save Template — {tpl_name}", form_fields)
            if result is None:
                clear_screen()
                _render(refreshed, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(refreshed, view_state)

            title = result["Title"].strip()
            if not title:
                clear_screen()
                _render(refreshed, view_state)
                _log("error", f"Title cannot be empty.")
                return CommandOutcome(refreshed, view_state)

            template_data = {"title": title}
            state_val = result.get("State", "").strip()
            if state_val:
                normalized_state = normalize_state_input(state_val)
                if normalized_state:
                    template_data["state"] = normalized_state
            priority_val = result.get("Priority", "").strip()
            if priority_val:
                normalized_priority = normalize_priority_input(priority_val)
                if normalized_priority:
                    template_data["priority"] = normalized_priority
            subtasks_input = result.get("Subtasks", "").strip()
            subtasks = [s.strip() for s in subtasks_input.split(",") if s.strip()] if subtasks_input else []
            if subtasks:
                template_data["subtasks"] = subtasks

            if save_template(tpl_name, template_data):
                clear_screen()
                _render(refreshed, view_state)
                _log("info", f"Template '{tpl_name}' saved.")
            else:
                _log("error", f"Could not save template.")
            return CommandOutcome(refreshed, view_state)

        # tpl del <name>
        del_match = re.match(r"^(?:del|delete|rm)\s+(\S+)$", arg, re.IGNORECASE)
        if del_match:
            tpl_name = del_match.group(1)
            if delete_template(tpl_name):
                _log("info", f"Template '{tpl_name}' deleted.")
            else:
                _log("error", f"Template '{tpl_name}' not found.")
            return CommandOutcome(refreshed, view_state)

        # tpl <name> — use template to create task
        tpl_data = get_template(arg)
        if not tpl_data:
            _log("error", f"Template '{arg}' not found. Use 'tpl' to list.")
            return CommandOutcome(refreshed, view_state)

        tpl_title = tpl_data.get("title", arg)
        tpl_state = tpl_data.get("state", DEFAULT_STATE)
        tpl_priority = tpl_data.get("priority")
        tpl_recurrence = tpl_data.get("recurrence")
        snapshot = read_journal_snapshot(context.journal_path)

        if add_task_to_file(context.journal_path, tpl_title, tpl_state, None, None, tpl_priority, tpl_recurrence):
            _save_undo_snapshot(context, snapshot)
            # Add subtasks if any
            tpl_subtasks = tpl_data.get("subtasks", [])
            if tpl_subtasks:
                refreshed_for_sub = context.refresh_tasks()
                parent = None
                for tasks in refreshed_for_sub.values():
                    for t in tasks:
                        if _strip_tags(t.title) == tpl_title:
                            parent = t
                            break
                    if parent:
                        break
                if parent:
                    for sub_title in tpl_subtasks:
                        add_subtask_to_task(context.journal_path, parent, sub_title, DEFAULT_STATE)

            updated_tasks = context.refresh_tasks()
            clear_screen()
            _log("info", f"Task created from template '{arg}'.")
            _render(updated_tasks, view_state)
            return CommandOutcome(updated_tasks, view_state)

        _log("error", f"Could not create task from template.")
        return CommandOutcome(refreshed, view_state)

    # ─── Time Tracking ─────────────────────────────────────────────────
    if re.match(r"^\s*(?:tt|time)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:tt|time)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: tt <id> <time|start|stop>")
            return CommandOutcome(refreshed, view_state)

        task_id = match.group(1)
        time_arg = match.group(2).strip().lower()
        target = find_task_by_id(refreshed, task_id)
        if not target or isinstance(target, Subtask):
            _log("error", f"Task {task_id} not found (must be parent task).")
            return CommandOutcome(refreshed, view_state)

        if time_arg == "start":
            # Store start timestamp in memory (session only)
            if not hasattr(context, '_time_tracking'):
                context._time_tracking = {}
            context._time_tracking[task_id] = time.time()
            _log("info", f"Timer started for task {task_id}.")
            return CommandOutcome(refreshed, view_state)

        if time_arg == "stop":
            if not hasattr(context, '_time_tracking') or task_id not in context._time_tracking:
                _log("error", f"No timer running for task {task_id}. Use 'tt {task_id} start' first.")
                return CommandOutcome(refreshed, view_state)
            elapsed = time.time() - context._time_tracking.pop(task_id)
            elapsed_minutes = max(1, int(elapsed / 60 + 0.5))
            time_arg = format_time_spent(elapsed_minutes)
            _log("info", f"Timer stopped: {time_arg} elapsed.")

        # Parse and add time
        new_minutes = parse_time_spent(time_arg)
        if new_minutes is None:
            _log("error", f"Invalid time: {time_arg}. Use format like 2h, 30m, 1h30m.")
            return CommandOutcome(refreshed, view_state)

        # Update the journal line
        snapshot = read_journal_snapshot(context.journal_path)
        existing_time = target.time_spent or 0
        if _log_time_to_task(context, target, new_minutes):
            _save_undo_snapshot(context, snapshot)
            _log("info", f"Logged {format_time_spent(new_minutes)} to task {task_id} (total: {format_time_spent(existing_time + new_minutes)}).")
        else:
            _log("error", f"Could not update time in journal.")

        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, view_state)

    # ─── Task Dependencies / Blockers ──────────────────────────────────
    # block del <blocked_id> <blocker_id> — remove a specific blocker
    if re.match(r"^\s*(?:block|blocker)\s+del\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:block|blocker)\s+del\s+(\S+)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: block del <blocked_id> <blocker_id>")
            return CommandOutcome(refreshed, view_state)

        blocked_id = match.group(1)
        blocker_id = match.group(2)

        blocked_task = find_task_by_id(refreshed, blocked_id)
        blocker_task = find_task_by_id(refreshed, blocker_id)

        if not blocked_task or isinstance(blocked_task, Subtask):
            _log("error", f"Task {blocked_id} not found.")
            return CommandOutcome(refreshed, view_state)
        if not blocker_task or isinstance(blocker_task, Subtask):
            _log("error", f"Task {blocker_id} not found.")
            return CommandOutcome(refreshed, view_state)

        from tm_features import remove_blocker_metadata, remove_blocks_metadata

        snapshot = read_journal_snapshot(context.journal_path)
        lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
        updated = False

        # Remove blockedby: from the blocked task
        if blocked_task.source_line:
            idx = blocked_task.source_line - 1
            if 0 <= idx < len(lines):
                lines[idx] = remove_blocker_metadata(lines[idx], _strip_tags(blocker_task.title))
                updated = True

        # Remove blocks: from the blocker task
        if updated and blocker_task.source_line:
            idx = blocker_task.source_line - 1
            if 0 <= idx < len(lines):
                lines[idx] = remove_blocks_metadata(lines[idx], _strip_tags(blocked_task.title))

        if updated:
            Path(context.journal_path).write_text("\n".join(lines), encoding="utf-8")
            _notify_post_write()
            _save_undo_snapshot(context, snapshot)
            _log("info", f"Removed blocker: {blocker_id} no longer blocks {blocked_id}.")
            clear_screen()
            refreshed = context.refresh_tasks()
            _render(refreshed, view_state)
        else:
            _log("error", f"Could not remove blocker.")

        return CommandOutcome(context.refresh_tasks(), view_state)

    # unblock <id> — remove ALL blockers from a task (or show picker if no ID)
    if re.match(r"^\s*unblock\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*unblock\s+(\S+)\s*$", raw_command, re.IGNORECASE)

        if not match:
            # No ID given — show interactive list of blocked tasks
            from tm_features import extract_blockers_from_line

            lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
            blocked_tasks = []
            for tasks in refreshed.values():
                for task in tasks:
                    if task.source_line:
                        idx = task.source_line - 1
                        if 0 <= idx < len(lines):
                            blockers = extract_blockers_from_line(lines[idx])
                            if blockers:
                                blocked_tasks.append((task, blockers))

            if not blocked_tasks:
                _log("info", f"No blocked tasks found.")
                return CommandOutcome(refreshed, view_state)

            # Show list picker (vertical, multi-select)
            from tm_form import show_list_picker
            import shutil as _shutil
            _cols = _shutil.get_terminal_size().columns
            # Max text width: terminal - 14 (borders, indicator, checkbox, padding)
            _max_opt = max(20, _cols - 14)
            options = []
            for t, b in blocked_tasks:
                label = f"[{t.task_id}] {_strip_tags(t.title)} (← {', '.join(b)})"
                if len(label) > _max_opt:
                    label = label[:_max_opt - 1] + "…"
                options.append(label)

            selected_indices = show_list_picker("Unblock — select tasks", options, multi=True)
            if not selected_indices:
                clear_screen()
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)

            # Process all selected tasks
            from tm_features import (
                extract_blockers_from_line, remove_all_blocker_metadata,
                remove_blocks_metadata, find_task_by_title_match,
            )
            snapshot = read_journal_snapshot(context.journal_path)
            lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
            total_removed = 0

            for sel_idx in selected_indices:
                target, _ = blocked_tasks[sel_idx]
                if not target.source_line:
                    continue
                idx = target.source_line - 1
                if idx < 0 or idx >= len(lines):
                    continue
                blockers = extract_blockers_from_line(lines[idx])
                if not blockers:
                    continue
                # Remove all blockedby: from this task
                lines[idx] = remove_all_blocker_metadata(lines[idx])
                # Remove corresponding blocks: from each blocker task
                for blocker_title in blockers:
                    blocker_task = find_task_by_title_match(refreshed, blocker_title)
                    if blocker_task and blocker_task.source_line:
                        b_idx = blocker_task.source_line - 1
                        if 0 <= b_idx < len(lines):
                            lines[b_idx] = remove_blocks_metadata(lines[b_idx], _strip_tags(target.title))
                total_removed += len(blockers)

            Path(context.journal_path).write_text("\n".join(lines), encoding="utf-8")
            _notify_post_write()
            _save_undo_snapshot(context, snapshot)
            _log("info", f"Removed {total_removed} blocker(s) from {len(selected_indices)} task(s).")
            clear_screen()
            refreshed = context.refresh_tasks()
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)
        else:
            task_id = match.group(1)
            target = find_task_by_id(refreshed, task_id)

        if not target or isinstance(target, Subtask):
            _log("error", f"Task {task_id} not found.")
            return CommandOutcome(refreshed, view_state)

        from tm_features import (
            extract_blockers_from_line, remove_all_blocker_metadata,
            remove_blocks_metadata, find_task_by_title_match,
        )

        snapshot = read_journal_snapshot(context.journal_path)
        lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")

        if not target.source_line:
            _log("error", f"Could not locate task in file.")
            return CommandOutcome(refreshed, view_state)

        idx = target.source_line - 1
        if idx < 0 or idx >= len(lines):
            _log("error", f"Could not locate task in file.")
            return CommandOutcome(refreshed, view_state)

        # Get blocker titles before removing
        blockers = extract_blockers_from_line(lines[idx])
        if not blockers:
            _log("info", f"Task {task_id} has no blockers.")
            return CommandOutcome(refreshed, view_state)

        # Remove all blockedby: from this task
        lines[idx] = remove_all_blocker_metadata(lines[idx])

        # Remove corresponding blocks: from each blocker task
        for blocker_title in blockers:
            blocker_task = find_task_by_title_match(refreshed, blocker_title)
            if blocker_task and blocker_task.source_line:
                b_idx = blocker_task.source_line - 1
                if 0 <= b_idx < len(lines):
                    lines[b_idx] = remove_blocks_metadata(lines[b_idx], _strip_tags(target.title))

        Path(context.journal_path).write_text("\n".join(lines), encoding="utf-8")
        _notify_post_write()
        _save_undo_snapshot(context, snapshot)
        _log("info", f"Removed {len(blockers)} blocker(s) from task {task_id}.")
        clear_screen()
        refreshed = context.refresh_tasks()
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    if re.match(r"^\s*(?:block|blocker)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:block|blocker)\s+(\S+)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            from tm_form import show_form, TextField
            # Pre-fill if partial ID was given
            partial = re.match(r"^\s*(?:block|blocker)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
            form_fields = [
                TextField("Blocked ID", value=partial.group(1) if partial else "", placeholder="ID of task being blocked"),
                TextField("Blocker ID", placeholder="ID of blocking task"),
            ]
            result = show_form("Block Dependency", form_fields)
            if result is None:
                clear_screen()
                _render(refreshed, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(refreshed, view_state)
            blocked_id = result["Blocked ID"].strip()
            blocker_id = result["Blocker ID"].strip()
            if not blocked_id or not blocker_id:
                clear_screen()
                _render(refreshed, view_state)
                _log("error", f"Both IDs are required.")
                return CommandOutcome(refreshed, view_state)
        else:
            blocked_id = match.group(1)
            blocker_id = match.group(2)

        blocked_task = find_task_by_id(refreshed, blocked_id)
        blocker_task = find_task_by_id(refreshed, blocker_id)

        if not blocked_task or isinstance(blocked_task, Subtask):
            _log("error", f"Task {blocked_id} not found.")
            return CommandOutcome(refreshed, view_state)
        if not blocker_task or isinstance(blocker_task, Subtask):
            _log("error", f"Task {blocker_id} not found.")
            return CommandOutcome(refreshed, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
        updated = False

        # Add blockedby: to the blocked task using source_line
        if blocked_task.source_line:
            idx = blocked_task.source_line - 1
            if 0 <= idx < len(lines):
                lines[idx] = add_blocker_metadata(lines[idx], _strip_tags(blocker_task.title))
                updated = True

        if updated and blocker_task.source_line:
            # Add blocks: to the blocker task using source_line
            idx = blocker_task.source_line - 1
            if 0 <= idx < len(lines):
                lines[idx] = add_blocks_metadata(lines[idx], _strip_tags(blocked_task.title))

        if updated:
            Path(context.journal_path).write_text("\n".join(lines), encoding="utf-8")
            _notify_post_write()
            _save_undo_snapshot(context, snapshot)
            _log("info", f"Task {blocked_id} is now blocked by task {blocker_id}.")
        else:
            _log("error", f"Could not update dependency.")

        updated_tasks = context.refresh_tasks()
        clear_screen()
        _render(updated_tasks, view_state)
        return CommandOutcome(updated_tasks, view_state)

    # ─── Pomodoro ──────────────────────────────────────────────────────
    if re.match(r"^\s*(?:pom|pomodoro)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:pom|pomodoro)(?:\s+(\S+))?(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
        task_id = match.group(1) if match and match.group(1) else None
        minutes = int(match.group(2)) if match and match.group(2) else 25

        target = None
        task_title = ""
        if task_id:
            target = find_task_by_id(refreshed, task_id)
            if not target:
                _log("error", f"Task {task_id} not found.")
                return CommandOutcome(refreshed, view_state)
            task_title = target.title

        elapsed = run_pomodoro(minutes, task_title)

        # Log time to task if specified
        if target and task_id and not isinstance(target, Subtask):
            snapshot = read_journal_snapshot(context.journal_path)
            if _log_time_to_task(context, target, elapsed):
                _save_undo_snapshot(context, snapshot)
                _log("info", f"Logged {format_time_spent(elapsed)} to task {task_id}.")

        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, view_state)

    # ─── Burndown Chart ────────────────────────────────────────────────
    if re.match(r"^\s*(?:bd|burndown)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:bd|burndown)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
        days = int(match.group(1)) if match and match.group(1) else 14
        chart = generate_burndown(refreshed, days)
        print(f"\n{Colors.HEADER}{chart}{Colors.RESET}")
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # ─── Kanban view ───────────────────────────────────────────────────
    if command in ("kb", "kanban"):
        refreshed = context.refresh_tasks()
        print(f"\n{Colors.HEADER}{Colors.BOLD}Kanban Board{Colors.RESET}\n")
        print(render_kanban(refreshed))
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # ─── Project/Tag view ──────────────────────────────────────────────
    if re.match(r"^\s*(?:pj|project)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:pj|project)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        tag_arg = match.group(1).strip() if match and match.group(1) else None

        if not tag_arg:
            # List all tags
            all_tags = get_all_tags(refreshed)
            if not all_tags:
                _log("info", f"No tags found in tasks.")
                return CommandOutcome(refreshed, view_state, skip_redraw=True)
            print(f"\n{Colors.HEADER}{Colors.BOLD}Project Tags{Colors.RESET}")
            print(f"{Colors.HEADER}{'─' * 40}{Colors.RESET}")
            for tag, count in sorted(all_tags.items(), key=lambda x: x[1], reverse=True):
                print(f"  #{tag:<20} {count} task(s)")
            print(f"{Colors.HEADER}{'─' * 40}{Colors.RESET}")
            return CommandOutcome(refreshed, view_state, skip_redraw=True)

        # Show tasks for specific tag
        tag = tag_arg.lstrip("#")
        tasks = get_tasks_by_tag(refreshed, tag)
        if not tasks:
            _log("info", f"No tasks found with tag #{tag}.")
            return CommandOutcome(refreshed, view_state, skip_redraw=True)

        tw = shutil.get_terminal_size((80, 24)).columns
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'─' * 3} #{tag} ({len(tasks)} tasks) {'─' * max(0, tw - len(tag) - 18)}{Colors.RESET}")
        for task in tasks:
            state_color = _get_state_color_inline(task.state)
            task_id = task.task_id or "?"
            priority_badge = f" [P:{task.priority}]" if task.priority else ""
            due = f" [DUE:{task.due_date.strftime('%d/%m/%Y')}]" if task.due_date else ""
            print(
                f"  [{task_id}] {state_color}{task.state:<{11}}{Colors.RESET} "
                f"{_title_without_tags_cmd(task.title)}{Colors.DIM}{priority_badge}{due}{Colors.RESET}"
            )
            for st in task.subtasks:
                st_color = _get_state_color_inline(st.state)
                st_due = f" [DUE:{st.due_date.strftime('%d/%m/%Y')}]" if st.due_date else ""
                print(f"       + [{st.task_id}] {st_color}{st.state:<{11}}{Colors.RESET} {_title_without_tags_cmd(st.title)}{Colors.DIM}{st_due}{Colors.RESET}")
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # ─── Export ────────────────────────────────────────────────────────
    if re.match(r"^\s*export\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*export\s+(\w+)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            # Show form
            from tm_form import show_form, TextField, SelectField
            form_fields = [
                SelectField("Format", ["json", "csv", "md"]),
                TextField("File path", placeholder="(optional, auto-generated)"),
            ]
            result = show_form("Export Tasks", form_fields)
            if result is None:
                clear_screen()
                _render(refreshed, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(refreshed, view_state)
            fmt = result["Format"]
            filepath = result.get("File path", "").strip() or None
        else:
            fmt = match.group(1).lower()
            filepath = match.group(2).strip() if match.group(2) else None

        if fmt == "json":
            content = export_to_json(refreshed)
            ext = ".json"
        elif fmt == "csv":
            content = export_to_csv(refreshed)
            ext = ".csv"
        elif fmt in ("md", "markdown"):
            content = export_to_markdown(refreshed)
            ext = ".md"
        else:
            _log("error", f"Unsupported format: {fmt}. Use json, csv, or md.")
            return CommandOutcome(refreshed, view_state)

        if not filepath:
            journal_dir = Path(context.journal_path).parent
            filepath = str(journal_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}")

        try:
            Path(filepath).write_text(content, encoding="utf-8")
            _log("info", f"Exported to: {filepath}")
        except OSError as exc:
            _log("error", f"Export failed: {exc}")
        return CommandOutcome(refreshed, view_state)

    # ─── Import ────────────────────────────────────────────────────────
    if re.match(r"^\s*import\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*import\s+(.+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            _log("error", f"Usage: import <filepath>")
            return CommandOutcome(tasks_by_date, view_state)

        import_path = match.group(1).strip()
        try:
            json_text = Path(import_path).read_text(encoding="utf-8")
        except OSError as exc:
            _log("error", f"Cannot read file: {exc}")
            return CommandOutcome(tasks_by_date, view_state)

        new_lines = import_from_json(json_text)
        if not new_lines:
            _log("error", f"Could not parse JSON or file is empty.")
            return CommandOutcome(tasks_by_date, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        try:
            with open(context.journal_path, "a", encoding="utf-8") as f:
                f.writelines(new_lines)
            _notify_post_write()
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            task_count = sum(1 for line in new_lines if line.strip().startswith("-"))
            _log("info", f"Imported {task_count} task(s) from {import_path}.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)
        except OSError as exc:
            _log("error", f"Import failed: {exc}")
            return CommandOutcome(tasks_by_date, view_state)

    # ─── Weekly Report ─────────────────────────────────────────────────
    if re.match(r"^\s*(?:wr|weekly)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:wr|weekly)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
        days = int(match.group(1)) if match and match.group(1) else int(get_setting("weekly_report_days", 7))
        report = generate_weekly_report(refreshed, days)
        print(f"\n{report}")
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # ─── Sort ──────────────────────────────────────────────────────────
    if re.match(r"^\s*sort\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*sort\s+(\w+)(?:\s+(asc|desc))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            # Show form
            from tm_form import show_form, SelectField
            _sort_options = ["priority", "due_date", "state", "none"]
            _dir_options = ["asc", "desc"]
            cur_sort_idx = _sort_options.index(view_state.sort_by) if view_state.sort_by in _sort_options else 3
            cur_dir_idx = _dir_options.index(view_state.sort_direction) if view_state.sort_direction in _dir_options else 0
            form_fields = [
                SelectField("Sort by", _sort_options, selected=cur_sort_idx),
                SelectField("Direction", _dir_options, selected=cur_dir_idx),
            ]
            result = show_form("Sort Tasks", form_fields)
            if result is None:
                clear_screen()
                _render(tasks_by_date, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(tasks_by_date, view_state)
            sort_by = result["Sort by"]
            direction = result["Direction"]
        else:
            sort_by = match.group(1).lower()
            if sort_by not in ("priority", "due_date", "state", "none"):
                _log("error", f"Invalid sort: {sort_by}. Use priority, due_date, state, or none.")
                return CommandOutcome(tasks_by_date, view_state)
            direction = match.group(2).lower() if match.group(2) else "asc"

        next_view = ViewState(
            show_done=view_state.show_done,
            only_in_progress=view_state.only_in_progress,
            only_testing=view_state.only_testing,
            search_query=view_state.search_query,
            sort_by=sort_by,
            sort_direction=direction,
        )
        updated_tasks = _refresh_and_render(context, next_view)
        _log("info", f"Sort: {sort_by} {direction}")
        return CommandOutcome(updated_tasks, next_view)

    if command in ("h", "help", "?"):
        print_help()
        return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)

    if command in ("i", "progress"):
        next_view = ViewState(
            show_done=False,
            only_in_progress=True,
            only_testing=False,
            search_query=view_state.search_query,
            sort_by=view_state.sort_by,
            sort_direction=view_state.sort_direction,
        )
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, next_view)

    if command in ("t", "testing"):
        next_view = ViewState(
            show_done=False,
            only_in_progress=False,
            only_testing=True,
            search_query=view_state.search_query,
            sort_by=view_state.sort_by,
            sort_direction=view_state.sort_direction,
        )
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, next_view)

    if command == "":
        return CommandOutcome(tasks_by_date, view_state)

    # ─── Sync commands ─────────────────────────────────────────────────
    if command == "sync":
        from tm_sync import sync_push_blocking, is_configured
        if not is_configured():
            _log("info", f"Sync not configured. Use 'config sync' to set up.")
            return CommandOutcome(tasks_by_date, view_state)
        else:
            sync_push_blocking()
            # Refresh tasks — pull may have brought new data
            updated_tasks = context.refresh_tasks()
            return CommandOutcome(updated_tasks, view_state)

    if command == "config sync":
        from tm_sync import run_config_wizard, init_sync, sync_push_async, is_configured
        from tm_settings import load_settings, save_settings
        from tm_journal import register_post_write_hook

        script_dir = Path(context.journal_path).parent.parent
        journals_dir = Path(context.journal_path).parent

        sync_config = run_config_wizard(script_dir, journals_dir)
        if sync_config:
            # Update settings file
            settings = load_settings(script_dir, force_reload=True)
            settings["sync"] = sync_config
            save_settings(settings, script_dir)

            # Activate sync if not already active
            if not is_configured():
                if init_sync(journals_dir, settings, script_dir):
                    register_post_write_hook(sync_push_async)

            _log("info", f"Sync configuration saved to .ttm_config")
        return CommandOutcome(tasks_by_date, view_state)

    if command == "sync status":
        from tm_sync import sync_status
        print(f"  {sync_status()}")
        return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)

    # ─── Log commands ──────────────────────────────────────────────────
    if command in ("show log", "log show", "log on"):
        from tm_log import set_visible, setup_scroll_region, render_log
        set_visible(True)
        setup_scroll_region()
        render_log()
        updated_tasks = _refresh_and_render(context, view_state)
        return CommandOutcome(updated_tasks, view_state)

    if command in ("hide log", "log hide", "log off"):
        from tm_log import set_visible
        set_visible(False)
        updated_tasks = _refresh_and_render(context, view_state)
        return CommandOutcome(updated_tasks, view_state)

    if command in ("log clear", "clear log"):
        from tm_log import clear
        clear()
        updated_tasks = _refresh_and_render(context, view_state)
        return CommandOutcome(updated_tasks, view_state)

    _log("error", "Unknown command. Type 'help' for available commands.")
    return CommandOutcome(tasks_by_date, view_state)
