"""Command dispatch and use-case handlers for the Task Manager CLI."""

import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from tm_config import DEFAULT_STATE, VALID_PRIORITIES, VALID_RECURRENCES, RECURRENCE_ALIASES
from tm_email import EmailConfig, EmailResult, send_email_report
from tm_features import (
    compute_next_recurrence_date,
    export_to_csv,
    export_to_json,
    export_to_markdown,
    generate_weekly_report,
    get_all_tags,
    get_tasks_by_tag,
    import_from_json,
    render_kanban,
    sort_tasks,
)
from tm_journal import (
    add_note_to_task_in_file,
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
from tm_settings import get_setting
from tm_ui import Colors, clear_screen, display_stats, display_tasks, print_help, prompt_for_state


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
        "syntax": "e <id|id:n#> <new text>",
        "description": "Edit task, subtask, or note text.",
        "examples": ["e 3 Update task title", "e 3:n1 New note text"],
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
        "syntax": "md <id|id.n|id:n#> [--due dd/mm/yyyy|none] [--priority <level>|none] [--tags <list>|none]",
        "description": "Edit due/priority/tags on tasks; for subtasks/notes due-priority are stored inline.",
        "examples": [
            "md 3 --due 10/06/2026 --priority urgent",
            "md 3 --tags backend,qr",
            "md 3.1 --due 11/06/2026 --priority high --tags qa",
            "md 3:n1 --priority low --tags none",
        ],
    },
    "ag": {
        "syntax": "ag [days]",
        "description": "Show due-date agenda for next N days (default 7).",
        "examples": ["ag", "ag 14"],
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
    "meta": "md",
    "agenda": "ag",
    "check": "ck",
    "undo": "u",
    "find": "f",
    "send": "se",
    "kanban": "kb",
    "project": "pj",
    "weekly": "wr",
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
        print(f"{Colors.DIM}{result.message}{Colors.RESET}")
    elif result.status == "draft":
        print(f"{Colors.HEADER}{result.message}{Colors.RESET}")
    else:
        print(f"{Colors.ERROR}{result.message}{Colors.RESET}")


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
                    valid = ", ".join(priority.lower() for priority in VALID_PRIORITIES)
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
    if update_task_state_in_file(context.journal_path, parent, "DONE"):
        _save_undo_snapshot(context, snapshot)
        latest = context.refresh_tasks()
        clear_screen()
        print(f"{Colors.DIM}All subtasks are DONE. Parent task {parent_id} closed automatically.{Colors.RESET}")
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

    def _print_group(title: str, items: list[Task]) -> None:
        print(f"\n{Colors.HEADER}{title}{Colors.RESET}")
        if not items:
            print(f"  {Colors.DIM}(none){Colors.RESET}")
            return
        ordered = sorted(items, key=lambda item: item.due_date or datetime.max)
        for task in ordered:
            task_id = task.task_id or "?"
            due = task.due_date.strftime("%d/%m/%Y") if task.due_date else "-"
            priority = task.priority or "-"
            print(f"  [{task_id}] {task.title} | due {due} | priority {priority} | {task.state}")

    print(f"\n{Colors.HEADER}{Colors.BOLD}Agenda{Colors.RESET}")
    _print_group("Overdue", overdue)
    _print_group("Due Today", due_today)
    _print_group(f"Due Next {days_ahead} Days", due_soon)


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
        print(f"{Colors.ERROR}No help available for that command.{Colors.RESET}")
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
            return CommandOutcome(tasks_by_date, view_state)

    if command == "fc":
        next_view = ViewState(
            show_done=view_state.show_done,
            only_in_progress=view_state.only_in_progress,
            only_testing=view_state.only_testing,
            search_query=None,
        )
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("u", "undo"):
        if not context.undo_stack:
            print(f"{Colors.DIM}Nothing to undo.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        snapshot = context.undo_stack.pop()
        if restore_journal_snapshot(context.journal_path, snapshot):
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Undid last change.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not restore undo snapshot.{Colors.RESET}")
        return CommandOutcome(tasks_by_date, view_state)

    if re.match(r"^\s*(?:ag|agenda)(?:\s+\d+)?\s*$", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*(?:ag|agenda)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
        days = int(match.group(1)) if match and match.group(1) else 7
        if days < 1 or days > 90:
            print(f"{Colors.ERROR}Agenda days must be between 1 and 90.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)
        refreshed = context.refresh_tasks()
        _print_agenda(refreshed, days_ahead=days)
        return CommandOutcome(refreshed, view_state)

    if command in ("ck", "check"):
        findings = lint_journal(context.journal_path)
        if findings:
            print(f"\n{Colors.ERROR}{Colors.BOLD}Journal check found issues:{Colors.RESET}")
            for finding in findings:
                print(f"  - {finding}")
        else:
            print(f"{Colors.DIM}Journal check passed. No issues found.{Colors.RESET}")
        refreshed = context.refresh_tasks()
        return CommandOutcome(refreshed, view_state)

    if command in ("q", "quit", "exit"):
        return CommandOutcome(tasks_by_date, view_state, should_exit=True)

    if command in ("a", "all"):
        next_view = ViewState(show_done=True, search_query=view_state.search_query)
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("p", "pending"):
        next_view = ViewState(search_query=view_state.search_query)
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("s", "stats"):
        updated_tasks = context.refresh_tasks()
        display_stats(updated_tasks)
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:se|send\s+email)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        pending = get_pending_tasks(updated_tasks)
        if not pending:
            print(f"{Colors.DIM}No pending tasks to send.{Colors.RESET}")
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
            print(f"{Colors.ERROR}{parse_error}{Colors.RESET}")
            print(
                f"{Colors.DIM}Usage: n [title] [--state <state>] [--date dd/mm/yyyy] "
                f"[--due dd/mm/yyyy] [--priority <level>] [--recur <freq>]{Colors.RESET}"
            )
            return CommandOutcome(tasks_by_date, view_state)

        if not task_title:
            task_title = input(f"{Colors.BOLD}Task title: {Colors.RESET}").strip()

        if not task_title:
            print(f"{Colors.ERROR}Task title cannot be empty.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        task_state = task_state or DEFAULT_STATE
        snapshot = read_journal_snapshot(context.journal_path)

        if add_task_to_file(context.journal_path, task_title, task_state, target_date, due_date, priority, recurrence):
            _save_undo_snapshot(context, snapshot)
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
            print(f"{Colors.DIM}Task created in {task_state} for {created_date}{suffix}.{Colors.RESET}")
            _render(updated_tasks, view_state)
            return CommandOutcome(updated_tasks, view_state)

        print(f"{Colors.ERROR}Could not create task in file.{Colors.RESET}")
        return CommandOutcome(tasks_by_date, view_state)

    if re.match(r"^\s*(?:cs|change\s+state)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:cs|change\s+state)\s+(\S+)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: cs <task_id> [state]{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target_task = find_task_by_id(updated_tasks, requested_id)
        if not target_task:
            print(f"{Colors.ERROR}Task ID {requested_id} not found.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        selected_state = None
        requested_state = match.group(2)
        if requested_state:
            selected_state = normalize_state_input(requested_state)

        if not selected_state:
            if requested_state:
                print(f"{Colors.ERROR}Invalid state: {requested_state}{Colors.RESET}")
            selected_state = prompt_for_state()

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
                and selected_state in ("DONE", "CANCELLED")
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
                print(f"{Colors.DIM}Recurring task created for {next_date.strftime('%d/%m/%Y')}.{Colors.RESET}")

            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Task {requested_id} updated to {selected_state}.{Colors.RESET}")
            _render(refreshed, view_state)
            if parent_id:
                maybe_closed = _maybe_autoclose_parent(context, parent_id, view_state)
                if maybe_closed is not None:
                    refreshed = maybe_closed
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not update task in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:an|add\s+note)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:an|add\s+note)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: an <task_id> <note>{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        note_text = match.group(2).strip()

        if not note_text:
            print(f"{Colors.ERROR}Note cannot be empty.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target_task = find_task_by_id(updated_tasks, requested_id)
        if not target_task:
            print(f"{Colors.ERROR}Task ID {requested_id} not found.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        if isinstance(target_task, Subtask):
            print(f"{Colors.ERROR}Add note supports parent task IDs only.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if add_note_to_task_in_file(context.journal_path, target_task, note_text):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Note added to task {requested_id}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not add note in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:e|edit)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:e|edit)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: e <task_id|subtask_id|task_id:n#> <new text>{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        new_title = match.group(2).strip()
        if not new_title:
            print(f"{Colors.ERROR}New title cannot be empty.{Colors.RESET}")
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
                print(f"{Colors.DIM}Updated note {requested_id}.{Colors.RESET}")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)

            print(f"{Colors.ERROR}Could not edit note in file.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id)
        if target is None:
            print(f"{Colors.ERROR}ID {requested_id} not found.{Colors.RESET}")
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
            print(f"{Colors.DIM}Updated title for {requested_id}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not edit title in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:del|delete)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:del|delete)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: del <task_id|subtask_id|task_id:n#>{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        if not _confirm_action(f"Delete {requested_id}?"):
            print(f"{Colors.DIM}Delete cancelled.{Colors.RESET}")
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
                print(f"{Colors.DIM}Deleted note {requested_id}.{Colors.RESET}")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)
            print(f"{Colors.ERROR}Could not delete note in file.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id)
        if target is None:
            print(f"{Colors.ERROR}ID {requested_id} not found.{Colors.RESET}")
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
            print(f"{Colors.DIM}Deleted {requested_id}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not delete item in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:mv|move|reschedule)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:mv|move|reschedule)\s+(\S+)\s+(\d{1,2}/\d{1,2}/\d{4})\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: mv <task_id> <dd/mm/yyyy>{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target_date = _try_parse_date(match.group(2))
        if target_date is None:
            print(f"{Colors.ERROR}Invalid date. Use dd/mm/yyyy.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id)
        if target is None or isinstance(target, Subtask):
            print(f"{Colors.ERROR}Move supports parent task IDs only.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        if not _confirm_action(f"Move task {requested_id} to {target_date.strftime('%d/%m/%Y')}?"):
            print(f"{Colors.DIM}Move cancelled.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if move_task_to_date_in_file(context.journal_path, target, target_date):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Moved task {requested_id} to {target_date.strftime('%d/%m/%Y')}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not move task in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:dup|duplicate)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(
            r"^\s*(?:dup|duplicate)\s+(\S+)(?:\s+(\d{1,2}/\d{1,2}/\d{4}))?\s*$",
            raw_command,
            re.IGNORECASE,
        )
        if not match:
            print(f"{Colors.ERROR}Usage: dup <task_id> [dd/mm/yyyy]{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target = find_task_by_id(updated_tasks, requested_id)
        if target is None or isinstance(target, Subtask):
            print(f"{Colors.ERROR}Duplicate supports parent task IDs only.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target_date = _try_parse_date(match.group(2)) if match.group(2) else None
        if match.group(2) and target_date is None:
            print(f"{Colors.ERROR}Invalid date. Use dd/mm/yyyy.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if duplicate_task_in_file(context.journal_path, target, target_date):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Duplicated task {requested_id}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not duplicate task in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:das|done\s+all\s+subtasks)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        match = re.match(r"^\s*(?:das|done\s+all\s+subtasks)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: das <task_id>{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        requested_id = match.group(1).strip()
        target = find_task_by_id(updated_tasks, requested_id)
        if target is None or isinstance(target, Subtask):
            print(f"{Colors.ERROR}Done-all-subtasks supports parent task IDs only.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        if not target.subtasks:
            print(f"{Colors.ERROR}Task {requested_id} has no subtasks.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        if mark_all_subtasks_done_in_file(context.journal_path, target):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}All subtasks in {requested_id} updated to DONE.{Colors.RESET}")
            _render(refreshed, view_state)
            maybe_closed = _maybe_autoclose_parent(context, requested_id, view_state)
            if maybe_closed is not None:
                refreshed = maybe_closed
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not update subtasks in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

    if re.match(r"^\s*(?:ar|archive)\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*(?:ar|archive)(?:\s+(\d{1,2}/\d{1,2}/\d{4}))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: ar [dd/mm/yyyy]{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        before_date = _try_parse_date(match.group(1)) if match.group(1) else None
        if match.group(1) and before_date is None:
            print(f"{Colors.ERROR}Invalid date. Use dd/mm/yyyy.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        date_label = before_date.strftime('%d/%m/%Y') if before_date else 'all dates'
        if not _confirm_action(f"Archive finished tasks up to {date_label}?"):
            print(f"{Colors.DIM}Archive cancelled.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        archive_path = _default_archive_path(context.journal_path)
        snapshot = read_journal_snapshot(context.journal_path)
        moved = archive_finished_tasks_in_file(context.journal_path, archive_path, before_date)
        if moved > 0:
            _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        print(f"{Colors.DIM}Archived {moved} finished task(s) to {archive_path}.{Colors.RESET}")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    if re.match(r"^\s*(?:md|meta)\b", raw_command, re.IGNORECASE):
        updated_tasks = context.refresh_tasks()
        requested_id, has_due, due_date, has_priority, priority, has_tags, tags, parse_error = _parse_meta_command(raw_command)
        if parse_error:
            print(f"{Colors.ERROR}{parse_error}{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        note_target = find_note_by_id(updated_tasks, requested_id or "")
        if note_target is not None:
            task, note_index, note_text = note_target
            base_text, existing_tags, existing_due, existing_priority = _extract_inline_meta(note_text)
            next_tags = tags or [] if has_tags else existing_tags
            next_due = due_date if has_due else existing_due
            next_priority = priority if has_priority else existing_priority
            next_text = _render_inline_meta_text(base_text, next_tags, next_due, next_priority)
            snapshot = read_journal_snapshot(context.journal_path)
            if edit_note_in_file(context.journal_path, task, note_index, next_text):
                _save_undo_snapshot(context, snapshot)
                refreshed = context.refresh_tasks()
                clear_screen()
                print(f"{Colors.DIM}Updated metadata for {requested_id}.{Colors.RESET}")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)

            print(f"{Colors.ERROR}Could not update note metadata in file.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id or "")
        if target is None:
            print(f"{Colors.ERROR}ID {requested_id} not found.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

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
                print(f"{Colors.DIM}Updated metadata for {requested_id}.{Colors.RESET}")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)

            print(f"{Colors.ERROR}Could not update subtask metadata in file.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        next_due = due_date if has_due else target.due_date
        next_priority = priority if has_priority else target.priority
        snapshot = read_journal_snapshot(context.journal_path)

        if has_tags:
            next_title = _apply_tags_to_text(target.title, tags or [])
            if not edit_task_title_in_file(context.journal_path, target, next_title):
                print(f"{Colors.ERROR}Could not update task tags in file.{Colors.RESET}")
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
            print(
                f"{Colors.DIM}Updated metadata for {requested_id}: due={due_label}, "
                f"priority={priority_label}, tags={'updated' if has_tags else 'unchanged'}.{Colors.RESET}"
            )
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not update metadata in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

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
        print(f"{Colors.DIM}Refreshed!{Colors.RESET}")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    # ─── Kanban view ───────────────────────────────────────────────────
    if command in ("kb", "kanban"):
        refreshed = context.refresh_tasks()
        print(f"\n{Colors.HEADER}{Colors.BOLD}Kanban Board{Colors.RESET}\n")
        print(render_kanban(refreshed))
        return CommandOutcome(refreshed, view_state)

    # ─── Project/Tag view ──────────────────────────────────────────────
    if re.match(r"^\s*(?:pj|project)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:pj|project)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        tag_arg = match.group(1).strip() if match and match.group(1) else None

        if not tag_arg:
            # List all tags
            all_tags = get_all_tags(refreshed)
            if not all_tags:
                print(f"{Colors.DIM}No tags found in tasks.{Colors.RESET}")
                return CommandOutcome(refreshed, view_state)
            print(f"\n{Colors.HEADER}{Colors.BOLD}Project Tags{Colors.RESET}")
            print(f"{Colors.HEADER}{'─' * 40}{Colors.RESET}")
            for tag, count in sorted(all_tags.items(), key=lambda x: x[1], reverse=True):
                print(f"  #{tag:<20} {count} task(s)")
            print(f"{Colors.HEADER}{'─' * 40}{Colors.RESET}")
            return CommandOutcome(refreshed, view_state)

        # Show tasks for specific tag
        tag = tag_arg.lstrip("#")
        tasks = get_tasks_by_tag(refreshed, tag)
        if not tasks:
            print(f"{Colors.DIM}No tasks found with tag #{tag}.{Colors.RESET}")
            return CommandOutcome(refreshed, view_state)

        print(f"\n{Colors.HEADER}{Colors.BOLD}Project: #{tag} ({len(tasks)} tasks){Colors.RESET}")
        print(f"{Colors.HEADER}{'─' * 50}{Colors.RESET}")
        for task in tasks:
            state_color = _get_state_color_inline(task.state)
            priority = f" [{task.priority}]" if task.priority else ""
            due = f" (due: {task.due_date.strftime('%d/%m/%Y')})" if task.due_date else ""
            task_id = task.task_id or "?"
            print(f"  [{task_id}] {state_color}{task.state}{Colors.RESET} {task.title}{priority}{due}")
            for st in task.subtasks:
                st_color = _get_state_color_inline(st.state)
                st_due = f" (due: {st.due_date.strftime('%d/%m/%Y')})" if st.due_date else ""
                print(f"       + [{st.task_id}] {st_color}{st.state}{Colors.RESET} {st.title}{st_due}")
        print(f"{Colors.HEADER}{'─' * 50}{Colors.RESET}")
        return CommandOutcome(refreshed, view_state)

    # ─── Export ────────────────────────────────────────────────────────
    if re.match(r"^\s*export\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*export\s+(\w+)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: export <json|csv|md> [filepath]{Colors.RESET}")
            return CommandOutcome(refreshed, view_state)

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
            print(f"{Colors.ERROR}Unsupported format: {fmt}. Use json, csv, or md.{Colors.RESET}")
            return CommandOutcome(refreshed, view_state)

        if not filepath:
            journal_dir = Path(context.journal_path).parent
            filepath = str(journal_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}")

        try:
            Path(filepath).write_text(content, encoding="utf-8")
            print(f"{Colors.DIM}Exported to: {filepath}{Colors.RESET}")
        except OSError as exc:
            print(f"{Colors.ERROR}Export failed: {exc}{Colors.RESET}")
        return CommandOutcome(refreshed, view_state)

    # ─── Import ────────────────────────────────────────────────────────
    if re.match(r"^\s*import\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*import\s+(.+)\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: import <filepath>{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        import_path = match.group(1).strip()
        try:
            json_text = Path(import_path).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{Colors.ERROR}Cannot read file: {exc}{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        new_lines = import_from_json(json_text)
        if not new_lines:
            print(f"{Colors.ERROR}Could not parse JSON or file is empty.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        snapshot = read_journal_snapshot(context.journal_path)
        try:
            with open(context.journal_path, "a", encoding="utf-8") as f:
                f.writelines(new_lines)
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            task_count = sum(1 for line in new_lines if line.strip().startswith("-"))
            print(f"{Colors.DIM}Imported {task_count} task(s) from {import_path}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)
        except OSError as exc:
            print(f"{Colors.ERROR}Import failed: {exc}{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

    # ─── Weekly Report ─────────────────────────────────────────────────
    if re.match(r"^\s*(?:wr|weekly)\b", raw_command, re.IGNORECASE):
        refreshed = context.refresh_tasks()
        match = re.match(r"^\s*(?:wr|weekly)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
        days = int(match.group(1)) if match and match.group(1) else int(get_setting("weekly_report_days", 7))
        report = generate_weekly_report(refreshed, days)
        print(f"\n{Colors.HEADER}{report}{Colors.RESET}")
        return CommandOutcome(refreshed, view_state)

    # ─── Sort ──────────────────────────────────────────────────────────
    if re.match(r"^\s*sort\b", raw_command, re.IGNORECASE):
        match = re.match(r"^\s*sort\s+(\w+)(?:\s+(asc|desc))?\s*$", raw_command, re.IGNORECASE)
        if not match:
            print(f"{Colors.ERROR}Usage: sort <priority|due_date|state|none> [asc|desc]{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        sort_by = match.group(1).lower()
        if sort_by not in ("priority", "due_date", "state", "none"):
            print(f"{Colors.ERROR}Invalid sort: {sort_by}. Use priority, due_date, state, or none.{Colors.RESET}")
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
        print(f"{Colors.DIM}Sort: {sort_by} {direction}{Colors.RESET}")
        return CommandOutcome(updated_tasks, next_view)

    if command in ("h", "help", "?"):
        print_help()
        return CommandOutcome(tasks_by_date, view_state)

    if command in ("i", "progress"):
        next_view = ViewState(
            show_done=False,
            only_in_progress=True,
            only_testing=False,
            search_query=view_state.search_query,
        )
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("t", "testing"):
        next_view = ViewState(
            show_done=False,
            only_in_progress=False,
            only_testing=True,
            search_query=view_state.search_query,
        )
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command == "":
        return CommandOutcome(tasks_by_date, view_state)

    print(f"{Colors.ERROR}Unknown command. Type 'help' for available commands.{Colors.RESET}")
    return CommandOutcome(tasks_by_date, view_state)
