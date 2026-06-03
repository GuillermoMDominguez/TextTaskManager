"""Terminal UI rendering and interaction helpers."""

import os
import re
from typing import Optional

try:
    import readline
except ImportError:  # pragma: no cover - readline is unavailable on some platforms
    readline = None

from tm_config import VALID_STATES
from tm_logic import build_note_id, get_id_width, normalize_state_input, task_matches_search
from tm_models import extract_tags_from_text


TAG_CLEAN_PATTERN = re.compile(r"(?<!\w)#[A-Za-z0-9_-]+")
STATE_COLUMN_WIDTH = max(len(state) for state in VALID_STATES)
TITLE_COLUMN_WIDTH = 56


def _title_without_tags(text: str) -> str:
    """Return title text with hashtag tokens removed for cleaner aligned display."""
    cleaned = TAG_CLEAN_PATTERN.sub("", text)
    return " ".join(cleaned.split())


def _format_title_cell(text: str, width: int) -> str:
    """Return a title cell trimmed to max width without right padding."""
    if len(text) > width:
        return text[: max(0, width - 1)] + "~"
    return text


def _format_tags_suffix(text: str) -> str:
    """Return tags as a compact suffix with no internal padding."""
    tags = extract_tags_from_text(text)
    if not tags:
        return ""
    return f" [{' '.join(f'#{tag}' for tag in tags)}]"


def _format_task_meta_suffix(task) -> str:
    """Render compact due/priority badges for a task."""
    chunks = []
    if getattr(task, "priority", None):
        chunks.append(f"[P:{task.priority}]")
    if getattr(task, "due_date", None):
        chunks.append(f"[DUE:{task.due_date.strftime('%d/%m/%Y')}]")
    return f" {' '.join(chunks)}" if chunks else ""


def _max_id_length(tasks_by_date: dict) -> int:
    """Return the maximum ID length considering both tasks and subtasks."""
    max_len = 1
    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.task_id:
                max_len = max(max_len, len(task.task_id))
            for subtask in task.subtasks:
                if subtask.task_id:
                    max_len = max(max_len, len(subtask.task_id))
    return max_len


def _format_id_column(task_id: Optional[str], width: int, zero_fill_numeric: bool = False) -> str:
    """Render [ID] with alignment padding outside brackets."""
    value = task_id or "?"
    if zero_fill_numeric and value.isdigit():
        value = value.zfill(width)
    padding = " " * max(0, width - len(value))
    return f"[{Colors.BOLD}{value}{Colors.RESET}]{padding}"


def _format_state_column(state: str) -> str:
    """Render [STATE] with alignment padding outside brackets."""
    color = get_state_color(state)
    padding = " " * max(0, STATE_COLUMN_WIDTH - len(state))
    return f"[{color}{state}{Colors.RESET}]{padding}"


def _task_row_prefix(id_column: str, state_column: str) -> str:
    """Return the common prefix used by parent task rows before the title column."""
    return f"  {'  '} {id_column} {state_column} "


def _title_continuation_prefix(id_width: int) -> str:
    """Return a blank prefix that lands exactly at the parent title column."""
    id_placeholder = " " * (id_width + 2)
    state_placeholder = " " * (STATE_COLUMN_WIDTH + 2)
    return f"  {'  '} {id_placeholder} {state_placeholder} "


def enable_command_history(history_file: Optional[str] = None, max_items: int = 300) -> None:
    """Enable input history navigation with arrow keys and optional persistence."""
    if readline is None:
        return

    readline.parse_and_bind("\"\\e[A\": previous-history")
    readline.parse_and_bind("\"\\e[B\": next-history")
    readline.set_history_length(max_items)

    if history_file and os.path.exists(history_file):
        try:
            readline.read_history_file(history_file)
        except OSError:
            pass


def remember_command(command: str) -> None:
    """Add a command to readline history, avoiding empty and repeated entries."""
    if readline is None:
        return

    normalized = command.strip()
    if not normalized:
        return

    previous = None
    length = readline.get_current_history_length()
    if length > 0:
        previous = readline.get_history_item(length)

    if normalized != previous:
        readline.add_history(normalized)


def save_command_history(history_file: Optional[str]) -> None:
    """Persist command history to disk if readline support is available."""
    if readline is None or not history_file:
        return

    try:
        readline.write_history_file(history_file)
    except OSError:
        pass


def enable_windows_ansi() -> None:
    """Enable ANSI escape code support on Windows."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            std_output_handle = -11
            enable_vt_processing = 0x0004
            handle = kernel32.GetStdHandle(std_output_handle)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | enable_vt_processing)
        except Exception:
            pass


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    BACKLOG = "\033[90m"
    IN_PROGRESS = "\033[33m"
    WAITING = "\033[35m"
    TESTING = "\033[36m"
    DONE = "\033[32m"
    CANCELLED = "\033[91m"

    DATE = "\033[94m"
    TASK = "\033[97m"
    SUBTASK = "\033[92m"
    COMMENT = "\033[90m"
    HEADER = "\033[96m"
    ERROR = "\033[91m"


def get_state_color(state: str) -> str:
    """Get the color code for a state."""
    color_map = {
        "BACKLOG": Colors.BACKLOG,
        "IN PROGRESS": Colors.IN_PROGRESS,
        "WAITING": Colors.WAITING,
        "TESTING": Colors.TESTING,
        "DONE": Colors.DONE,
        "CANCELLED": Colors.CANCELLED,
    }
    return color_map.get(state, Colors.RESET)


def format_state(state: str) -> str:
    """Format a state with color and padding."""
    color = get_state_color(state)
    return f"{color}{state}{Colors.RESET}"


def display_tasks(
    tasks_by_date: dict,
    show_done: bool = False,
    only_in_progress: bool = False,
    only_testing: bool = False,
    search_query: Optional[str] = None,
) -> None:
    """Display tasks grouped by date in descending order."""
    id_width = max(get_id_width(tasks_by_date), _max_id_length(tasks_by_date))

    sorted_dates = sorted([d for d in tasks_by_date.keys() if d is not None], reverse=True)
    if None in tasks_by_date:
        sorted_dates.append(None)

    total_tasks = 0
    total_pending = 0

    for date in sorted_dates:
        tasks = tasks_by_date[date]

        if not show_done:
            if not only_in_progress:
                if not only_testing:
                    visible_tasks = [t for t in tasks if not t.is_finished()]
                else:
                    visible_tasks = [t for t in tasks if t.is_in_testing()]
            else:
                visible_tasks = [t for t in tasks if t.is_in_progress() or t.is_in_testing()]
        else:
            visible_tasks = tasks

        visible_tasks = [task for task in visible_tasks if task_matches_search(task, search_query)]

        if not visible_tasks:
            continue

        date_str = date.strftime("%A, %d/%m/%Y") if date else "No Date"

        print(f"\n{Colors.DATE}{Colors.BOLD}{'─' * 50}{Colors.RESET}")
        print(f"{Colors.DATE}{Colors.BOLD}  {date_str}{Colors.RESET}")
        print(f"{Colors.DATE}{'─' * 50}{Colors.RESET}")

        for task in visible_tasks:
            total_tasks += 1
            if not task.is_finished():
                total_pending += 1

            state_display = _format_state_column(task.state)
            task_id_display = _format_id_column(task.task_id, id_width, zero_fill_numeric=True)
            task_title_display = _title_without_tags(task.title)
            task_title_cell = _format_title_cell(task_title_display, TITLE_COLUMN_WIDTH)
            task_prefix = _task_row_prefix(task_id_display, state_display)
            continuation_prefix = _title_continuation_prefix(id_width)
            print(
                f"{task_prefix}"
                f"{Colors.TASK}{task_title_cell}{Colors.RESET}"
                f"{Colors.DIM}{_format_task_meta_suffix(task)}{_format_tags_suffix(task.title)}{Colors.RESET}"
            )

            for subtask in task.subtasks:
                subtask_state_display = _format_state_column(subtask.state)
                subtask_id_display = _format_id_column(subtask.task_id, id_width)
                subtask_title_display = _title_without_tags(subtask.title)
                subtask_title_cell = _format_title_cell(subtask_title_display, TITLE_COLUMN_WIDTH)
                print(
                    f"{continuation_prefix}{Colors.SUBTASK}+ {Colors.RESET}{subtask_id_display} {subtask_state_display} "
                    f"{Colors.SUBTASK}{subtask_title_cell}{Colors.RESET}"
                    f"{Colors.DIM}{_format_tags_suffix(subtask.title)}{Colors.RESET}"
                )

            for note_idx, comment in enumerate(task.comments, start=1):
                note_id = build_note_id(task.task_id or "?", note_idx)
                note_text_display = _title_without_tags(comment)
                note_title_cell = _format_title_cell(note_text_display, TITLE_COLUMN_WIDTH)
                print(
                    f"{continuation_prefix}{Colors.COMMENT}: [{note_id}] {note_title_cell}{Colors.RESET}"
                    f"{Colors.DIM}{_format_tags_suffix(comment)}{Colors.RESET}"
                )

    print(f"\n{Colors.HEADER}{'─' * 50}{Colors.RESET}")
    if show_done:
        print(f"{Colors.HEADER}  Total: {total_tasks} tasks ({total_pending} pending){Colors.RESET}")
    else:
        print(f"{Colors.HEADER}  Showing: {total_pending} pending tasks{Colors.RESET}")
    if search_query:
        print(f"{Colors.HEADER}  Search: {search_query}{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 50}{Colors.RESET}")


def get_stats(tasks_by_date: dict) -> dict:
    """Calculate statistics about tasks."""
    stats = {state: 0 for state in VALID_STATES}
    total = 0

    for tasks in tasks_by_date.values():
        for task in tasks:
            stats[task.state] = stats.get(task.state, 0) + 1
            total += 1

    return {"by_state": stats, "total": total}


def display_stats(tasks_by_date: dict) -> None:
    """Display task statistics."""
    stats = get_stats(tasks_by_date)

    print(f"\n{Colors.HEADER}{Colors.BOLD}Task Statistics{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 30}{Colors.RESET}")

    for state in VALID_STATES:
        count = stats["by_state"].get(state, 0)
        color = get_state_color(state)
        bar = "█" * count
        print(f"  {color}{state:12}{Colors.RESET} {count:3} {color}{bar}{Colors.RESET}")

    print(f"{Colors.HEADER}{'─' * 30}{Colors.RESET}")
    print(f"  {'Total':12} {stats['total']:3}")


def print_help() -> None:
    """Print help message."""
    command_width = 30
    rows = [
        ("a / all", "Show all tasks (including done)"),
        ("p / pending", "Show pending tasks only (default)"),
        ("i / progress", "Show in progress or testing tasks only"),
        ("t / testing", "Show in testing tasks only"),
        ("s / stats", "Show task statistics"),
        ("se / send email [recipient]", "Send pending tasks by email"),
        ("n / new", "Create task (default state: BACKLOG, default date: today)"),
        ("cs / change state <id> [state]", "Change task state by ID"),
        ("an / add note <id> <note>", "Add a note to a task by ID"),
        ("e / edit <id|id:n#> <new text>", "Edit task, subtask, or note"),
        ("del / delete <id|id:n#>", "Delete task, subtask, or note"),
        ("mv / move <id> <dd/mm/yyyy>", "Move task to another date section"),
        ("dup / duplicate <id> [dd/mm/yyyy]", "Clone task with notes/subtasks"),
        ("das / done all subtasks <id>", "Set all subtasks to DONE and auto-close parent"),
        ("ar / archive [dd/mm/yyyy]", "Archive finished tasks up to optional date"),
        ("md / meta <id> [--due ...] [--priority ...]", "Set/clear due and priority for task"),
        ("ag / agenda", "Show due-date agenda (overdue/today/next days)"),
        ("ck / check", "Lint journal structure and metadata"),
        ("u / undo", "Undo last journal mutation in this session"),
        ("f / find <text|#tag>", "Filter visible tasks by text or tag"),
        ("fc / find clear", "Clear active search filter"),
        ("r / refresh", "Reload file and refresh display"),
        ("h / help", "Show this help message"),
        ("q / quit", "Exit the application"),
    ]

    print(f"\n{Colors.HEADER}{Colors.BOLD}Task Manager - Commands{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 72}{Colors.RESET}")
    for command_text, description in rows:
        print(f"  {Colors.BOLD}{command_text.ljust(command_width)}{Colors.RESET} {description}")

    print(
        " "
        f"\n  {Colors.DIM}Usage for new:{Colors.RESET} "
        f"{Colors.BOLD}n [title] [--state <state>] [--date dd/mm/yyyy]{Colors.RESET}"
    )
    print(f"  {Colors.DIM}Note:{Colors.RESET} task IDs are generated per session and can change after refresh/restart.")
    print(f"{Colors.HEADER}{'─' * 72}{Colors.RESET}")


def prompt_for_state() -> str:
    """Show state options and ask the user to choose one."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}Select new state:{Colors.RESET}")
    for idx, state in enumerate(VALID_STATES, start=1):
        print(f"  {idx}. {state}")

    while True:
        choice = input(f"{Colors.BOLD}State (number or name): {Colors.RESET}").strip()
        if choice.isdigit():
            selected_idx = int(choice)
            if 1 <= selected_idx <= len(VALID_STATES):
                return VALID_STATES[selected_idx - 1]

        normalized = normalize_state_input(choice)
        if normalized:
            return normalized

        print(f"{Colors.ERROR}Invalid state. Try again.{Colors.RESET}")


def clear_screen() -> None:
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")
