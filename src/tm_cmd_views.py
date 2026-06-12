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
from .tm_logic import find_task_by_id, get_id_width, parse_date_input
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
        from .tm_settings import load_settings
        load_settings(force_reload=True)
        Colors._reload()
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
    from .tm_views_data import get_agenda_data, TaskViewItem

    data = get_agenda_data(tasks_by_date, days_ahead)

    def _print_group(title: str, items: list[TaskViewItem], icon: str = "") -> None:
        print(f"\n  {Colors.BOLD}{icon}{title}{Colors.RESET}")
        if not items:
            print(f"    {Colors.DIM}(none){Colors.RESET}")
            return
        for item in items:
            id_value = item.task_id.zfill(data.id_width) if item.task_id.isdigit() else item.task_id
            id_padding = " " * max(0, data.id_width - len(id_value))
            state_color = _get_state_color_inline(item.state)
            due_str = item.due_date.strftime("%d/%m/%Y") if item.due_date else ""
            priority_badge = f" [P:{item.priority}]" if item.priority else ""
            print(
                f"    [{Colors.BOLD}{id_value}{Colors.RESET}]{id_padding} {state_color}{item.state:<{11}}{Colors.RESET} "
                f"{item.title}{Colors.DIM}{priority_badge} [DUE:{due_str}]{Colors.RESET}"
            )

    tw = shutil.get_terminal_size((80, 24)).columns
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'─' * 3} Agenda {'─' * (tw - 12)}{Colors.RESET}")
    _print_group("Overdue", data.overdue, "⚠ ")
    _print_group("Due Today", data.due_today, "◉ ")
    _print_group(f"Due Next {data.days_ahead} Days", data.due_soon, "◌ ")


# ─── Calendar ─────────────────────────────────────────────────────────

def handle_calendar(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: cal, calendar [week] [dd/mm/yyyy].
    
    Examples:
        cal            - Show current month
        cal week       - Show current week
        cal 06/2026    - Show June 2026
        cal week 15/06/2026  - Show week containing 15/06/2026
    """
    if not re.match(r"^\s*cal(?:endar)?(?:\s+.*)?\s*$", raw_command, re.IGNORECASE):
        return None

    parts = raw_command.strip().split()
    view = "month"
    target_date = None

    # Parse arguments
    for part in parts[1:]:
        if part.lower() in ("week", "w"):
            view = "week"
        elif re.match(r"^\d{1,2}/\d{4}$", part):  # mm/yyyy
            month, year = part.split("/")
            target_date = datetime(int(year), int(month), 1)
        elif re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", part):  # dd/mm/yyyy
            target_date = parse_date_input(part)

    if target_date is None:
        target_date = datetime.now()

    refreshed = context.refresh_tasks()
    _print_calendar(refreshed, view=view, target_date=target_date)
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


def _print_calendar(tasks_by_date: dict, view: str = "month", target_date: datetime = None) -> None:
    """Print ASCII calendar with tasks."""
    from .tm_views_data import get_calendar_data
    import calendar

    if target_date is None:
        target_date = datetime.now()

    data = get_calendar_data(
        tasks_by_date,
        view=view,
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
    )

    tw = shutil.get_terminal_size((80, 24)).columns
    today = datetime.now().date()

    # Title
    if view == "week":
        title = f"Week of {data.start_date}"
    else:
        title = f"{calendar.month_name[data.month]} {data.year}"

    print(f"\n{Colors.HEADER}{Colors.BOLD}{'─' * 3} {title} {'─' * (tw - len(title) - 6)}{Colors.RESET}")

    # Weekday headers
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    col_width = max(12, (tw - 2) // 7)
    header = "".join(f"{Colors.BOLD}{d:^{col_width}}{Colors.RESET}" for d in weekdays)
    print(header)
    print("─" * tw)

    # Get sorted dates
    dates = sorted(data.days.keys(), key=lambda x: datetime.strptime(x, "%d/%m/%Y"))

    if view == "month":
        # Pad start of month to align with weekday
        first_date = datetime.strptime(dates[0], "%d/%m/%Y")
        start_pad = (first_date.weekday())  # Monday = 0
        dates = [None] * start_pad + dates

    # Print calendar grid
    row = []
    for i, date_str in enumerate(dates):
        if date_str is None:
            row.append(" " * col_width)
        else:
            d, m, y = date_str.split("/")
            date_obj = datetime(int(y), int(m), int(d)).date()
            tasks = data.days.get(date_str, [])

            # Day number with formatting
            is_today = date_obj == today
            is_weekend = date_obj.weekday() >= 5

            if is_today:
                day_str = f"{Colors.ACCENT}{Colors.BOLD}*{int(d)}{Colors.RESET}"
            elif is_weekend:
                day_str = f"{Colors.DIM}{int(d)}{Colors.RESET}"
            else:
                day_str = f"{int(d)}"

            # Task count indicator
            task_count = len(tasks)
            if task_count > 0:
                count_str = f" ({task_count})"
            else:
                count_str = ""

            cell = f"{day_str}{count_str}"
            # Pad cell (accounting for ANSI codes)
            visible_len = len(str(int(d))) + len(count_str) + (1 if is_today else 0)
            padding = col_width - visible_len
            row.append(cell + " " * max(0, padding))

        if len(row) == 7:
            print("".join(row))
            row = []

    # Print remaining row
    if row:
        while len(row) < 7:
            row.append(" " * col_width)
        print("".join(row))

    # Print task details for days with tasks (limited)
    print()
    task_dates = [(d, data.days[d]) for d in sorted(data.days.keys(), key=lambda x: datetime.strptime(x, "%d/%m/%Y")) if data.days[d]]

    if task_dates:
        print(f"{Colors.DIM}Tasks with due dates:{Colors.RESET}")
        for date_str, tasks in task_dates[:10]:  # Limit to 10 dates
            d, m, y = date_str.split("/")
            date_obj = datetime(int(y), int(m), int(d)).date()
            day_name = date_obj.strftime("%a")

            is_overdue = date_obj < today
            is_today_date = date_obj == today

            if is_overdue:
                date_color = Colors.ERROR
            elif is_today_date:
                date_color = Colors.SUCCESS
            else:
                date_color = Colors.DIM

            print(f"\n  {date_color}{Colors.BOLD}{day_name} {date_str}{Colors.RESET}")
            for task in tasks[:5]:  # Limit to 5 tasks per day
                state_color = _get_state_color_inline(task.state)
                prio = f" [P:{task.priority}]" if task.priority else ""
                print(f"    [{Colors.BOLD}{task.task_id}{Colors.RESET}] {state_color}{task.state:<11}{Colors.RESET} {task.title}{Colors.DIM}{prio}{Colors.RESET}")
            if len(tasks) > 5:
                print(f"    {Colors.DIM}... and {len(tasks) - 5} more{Colors.RESET}")
