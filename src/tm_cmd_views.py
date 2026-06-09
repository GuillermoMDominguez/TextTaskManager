"""View and display command handlers: all, pending, progress, testing, stats, help, clear, refresh, quit, agenda, day, find, fc, undo, check."""

import re
import shutil
from datetime import datetime, timedelta
from typing import Optional

from .tm_cmd_common import (
    CommandContext,
    CommandOutcome,
    ViewState,
    Colors,
    _get_state_color_inline,
    _log,
    _refresh_and_render,
    _render,
    _save_undo_snapshot,
    clear_screen,
    display_tasks,
)
from .tm_journal import lint_journal, read_journal_snapshot, restore_journal_snapshot
from .tm_logic import find_task_by_id, parse_date_input
from .tm_models import Task
from .tm_ui import display_stats, print_help


def handle_quit(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: q, quit, exit."""
    if command in ("q", "quit", "exit"):
        return CommandOutcome(tasks_by_date, view_state, should_exit=True)
    return None


def handle_clear(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: cls, clear."""
    if command in ("cls", "clear"):
        clear_screen()
        _render(tasks_by_date, view_state)
        return CommandOutcome(tasks_by_date, view_state)
    return None


def handle_empty(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle empty command (just pressing Enter)."""
    if command == "":
        return CommandOutcome(tasks_by_date, view_state)
    return None


def handle_help(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: h, help, ?."""
    if command in ("h", "help", "?"):
        print_help()
        return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)
    return None


def handle_refresh(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: r, refresh."""
    if command in ("r", "refresh"):
        refreshed = context.refresh_tasks()
        clear_screen()
        _log("info", f"Refreshed!")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)
    return None


def handle_view_all(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: a, all."""
    if command in ("a", "all"):
        next_view = ViewState(show_done=True, search_query=view_state.search_query, sort_by=view_state.sort_by, sort_direction=view_state.sort_direction)
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, next_view)
    return None


def handle_view_pending(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: p, pending."""
    if command in ("p", "pending"):
        next_view = ViewState(search_query=view_state.search_query, sort_by=view_state.sort_by, sort_direction=view_state.sort_direction)
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, next_view)
    return None


def handle_view_progress(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: i, progress."""
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
    return None


def handle_view_testing(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: t, testing."""
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
    return None


def handle_stats(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: s, stats."""
    if command in ("s", "stats"):
        updated_tasks = context.refresh_tasks()
        display_stats(updated_tasks)
        return CommandOutcome(updated_tasks, view_state, skip_redraw=True)
    return None


def handle_undo(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: u, undo."""
    if command not in ("u", "undo"):
        return None

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


def handle_filter_clear(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: fc — clear filter."""
    if command != "fc":
        return None

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


def handle_find(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: f, find — filter tasks."""
    if not re.match(r"^\s*(?:f|find)\b", raw_command, re.IGNORECASE):
        return None

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


def handle_agenda(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: ag, agenda."""
    if not re.match(r"^\s*(?:ag|agenda)(?:\s+\d+)?\s*$", raw_command, re.IGNORECASE):
        return None

    match = re.match(r"^\s*(?:ag|agenda)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
    days = int(match.group(1)) if match and match.group(1) else 7
    if days < 1 or days > 90:
        _log("error", f"Agenda days must be between 1 and 90.")
        return CommandOutcome(tasks_by_date, view_state)
    refreshed = context.refresh_tasks()
    _print_agenda(refreshed, days_ahead=days)
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


def handle_day(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: day, hoy, today."""
    if not re.match(r"^\s*(?:day|hoy|today)(?:\s+.+)?\s*$", raw_command, re.IGNORECASE):
        return None

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


def handle_check(command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: ck, check — journal linter."""
    if command not in ("ck", "check"):
        return None

    findings = lint_journal(context.journal_path)
    if findings:
        print(f"\n{Colors.ERROR}{Colors.BOLD}Journal check found issues:{Colors.RESET}")
        for finding in findings:
            print(f"  - {finding}")
    else:
        _log("info", f"Journal check passed. No issues found.")
    refreshed = context.refresh_tasks()
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


# ─── Agenda helper ─────────────────────────────────────────────────────────

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
