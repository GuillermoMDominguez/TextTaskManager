"""Command dispatch and use-case handlers for the Task Manager CLI."""

import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from tm_config import DEFAULT_STATE
from tm_email import EmailConfig, EmailResult, send_email_report
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
from tm_models import Subtask, Task
from tm_ui import Colors, clear_screen, display_stats, display_tasks, print_help, prompt_for_state


@dataclass
class ViewState:
    """Current task-list filter state."""

    show_done: bool = False
    only_in_progress: bool = False
    only_testing: bool = False
    search_query: Optional[str] = None


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
    """Render tasks using the current view state."""
    display_tasks(
        tasks_by_date,
        view_state.show_done,
        view_state.only_in_progress,
        view_state.only_testing,
        view_state.search_query,
    )


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
) -> tuple[Optional[str], bool, Optional[datetime], bool, Optional[str], Optional[str]]:
    """Parse metadata command and return task_id, due flag/value, priority flag/value, and error."""
    try:
        tokens = shlex.split(raw_command)
    except ValueError as exc:
        return None, False, None, False, None, f"Invalid command syntax: {exc}"

    if len(tokens) < 2:
        return None, False, None, False, None, "Usage: md <task_id> [--due dd/mm/yyyy|none] [--priority <level>|none]"

    task_id = tokens[1]
    due_date: Optional[datetime] = None
    priority: Optional[str] = None
    has_due = False
    has_priority = False

    idx = 2
    while idx < len(tokens):
        token = tokens[idx].lower()
        if token == "--due":
            idx += 1
            if idx >= len(tokens):
                return None, False, None, False, None, "Missing value for --due"
            has_due = True
            raw_due = tokens[idx]
            if raw_due.lower() != "none":
                due_date = _try_parse_date(raw_due)
                if due_date is None:
                    return None, False, None, False, None, f"Invalid due date: {raw_due}"
            idx += 1
            continue

        if token in ("--priority", "-p"):
            idx += 1
            if idx >= len(tokens):
                return None, False, None, False, None, "Missing value for --priority"
            has_priority = True
            raw_priority = tokens[idx]
            if raw_priority.lower() != "none":
                priority = normalize_priority_input(raw_priority)
                if priority is None:
                    return None, False, None, False, None, f"Invalid priority: {raw_priority}"
            idx += 1
            continue

        return None, False, None, False, None, f"Unknown option: {tokens[idx]}"

    if not has_due and not has_priority:
        return None, False, None, False, None, "Usage: md <task_id> [--due dd/mm/yyyy|none] [--priority <level>|none]"

    return task_id, has_due, due_date, has_priority, priority, None


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


def execute_command(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> CommandOutcome:
    """Execute a single user command and return updated state."""
    command = raw_command.lower()

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
        task_title, task_state, target_date, due_date, priority, parse_error = parse_new_command_args(raw_command)
        if parse_error:
            print(f"{Colors.ERROR}{parse_error}{Colors.RESET}")
            print(
                f"{Colors.DIM}Usage: n [title] [--state <state>] [--date dd/mm/yyyy] "
                f"[--due dd/mm/yyyy] [--priority <level>]{Colors.RESET}"
            )
            return CommandOutcome(tasks_by_date, view_state)

        if not task_title:
            task_title = input(f"{Colors.BOLD}Task title: {Colors.RESET}").strip()

        if not task_title:
            print(f"{Colors.ERROR}Task title cannot be empty.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        task_state = task_state or DEFAULT_STATE
        snapshot = read_journal_snapshot(context.journal_path)

        if add_task_to_file(context.journal_path, task_title, task_state, target_date, due_date, priority):
            _save_undo_snapshot(context, snapshot)
            updated_tasks = context.refresh_tasks()
            clear_screen()
            created_date = (target_date or datetime.now()).strftime("%d/%m/%Y")
            extra = []
            if due_date:
                extra.append(f"due {due_date.strftime('%d/%m/%Y')}")
            if priority:
                extra.append(f"priority {priority}")
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
        requested_id, has_due, due_date, has_priority, priority, parse_error = _parse_meta_command(raw_command)
        if parse_error:
            print(f"{Colors.ERROR}{parse_error}{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id or "")
        if target is None or isinstance(target, Subtask):
            print(f"{Colors.ERROR}Metadata update supports parent task IDs only.{Colors.RESET}")
            return CommandOutcome(updated_tasks, view_state)

        next_due = due_date if has_due else target.due_date
        next_priority = priority if has_priority else target.priority
        snapshot = read_journal_snapshot(context.journal_path)

        if update_task_metadata_in_file(context.journal_path, target, next_due, next_priority):
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            due_label = next_due.strftime("%d/%m/%Y") if next_due else "none"
            priority_label = next_priority or "none"
            print(f"{Colors.DIM}Updated metadata for {requested_id}: due={due_label}, priority={priority_label}.{Colors.RESET}")
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
