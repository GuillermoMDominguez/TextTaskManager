"""Command dispatch and use-case handlers for the Task Manager CLI."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from tm_config import DEFAULT_STATE
from tm_email import EmailConfig, EmailResult, send_email_report
from tm_journal import add_note_to_task_in_file, add_task_to_file, update_subtask_state_in_file, update_task_state_in_file
from tm_logic import build_pending_email_body, find_task_by_id, get_pending_tasks, normalize_state_input, parse_new_command_args
from tm_models import Subtask
from tm_ui import Colors, clear_screen, display_stats, display_tasks, print_help, prompt_for_state


@dataclass
class ViewState:
    """Current task-list filter state."""

    show_done: bool = False
    only_in_progress: bool = False
    only_testing: bool = False


@dataclass
class CommandContext:
    """Dependencies needed by command handlers."""

    journal_path: str
    email_config: EmailConfig
    refresh_tasks: Callable[[], dict]


@dataclass
class CommandOutcome:
    """Result returned by command execution."""

    tasks_by_date: dict
    view_state: ViewState
    should_exit: bool = False


def _render(tasks_by_date: dict, view_state: ViewState) -> None:
    """Render tasks using the current view state."""
    display_tasks(tasks_by_date, view_state.show_done, view_state.only_in_progress, view_state.only_testing)


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


def execute_command(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> CommandOutcome:
    """Execute a single user command and return updated state."""
    command = raw_command.lower()

    if command in ("q", "quit", "exit"):
        return CommandOutcome(tasks_by_date, view_state, should_exit=True)

    if command in ("a", "all"):
        next_view = ViewState(show_done=True)
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("p", "pending"):
        next_view = ViewState()
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
        task_title, task_state, target_date, parse_error = parse_new_command_args(raw_command)
        if parse_error:
            print(f"{Colors.ERROR}{parse_error}{Colors.RESET}")
            print(f"{Colors.DIM}Usage: n [title] [--state <state>] [--date dd/mm/yyyy]{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        if not task_title:
            task_title = input(f"{Colors.BOLD}Task title: {Colors.RESET}").strip()

        if not task_title:
            print(f"{Colors.ERROR}Task title cannot be empty.{Colors.RESET}")
            return CommandOutcome(tasks_by_date, view_state)

        task_state = task_state or DEFAULT_STATE

        if add_task_to_file(context.journal_path, task_title, task_state, target_date):
            updated_tasks = context.refresh_tasks()
            clear_screen()
            created_date = (target_date or datetime.now()).strftime("%d/%m/%Y")
            print(f"{Colors.DIM}Task created in {task_state} for {created_date}.{Colors.RESET}")
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

        if isinstance(target_task, Subtask):
            persisted = update_subtask_state_in_file(context.journal_path, target_task, selected_state)
        else:
            persisted = update_task_state_in_file(context.journal_path, target_task, selected_state)

        if persisted:
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Task {requested_id} updated to {selected_state}.{Colors.RESET}")
            _render(refreshed, view_state)
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

        if add_note_to_task_in_file(context.journal_path, target_task, note_text):
            refreshed = context.refresh_tasks()
            clear_screen()
            print(f"{Colors.DIM}Note added to task {requested_id}.{Colors.RESET}")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        print(f"{Colors.ERROR}Could not add note in file.{Colors.RESET}")
        return CommandOutcome(updated_tasks, view_state)

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
        next_view = ViewState(show_done=False, only_in_progress=True, only_testing=False)
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command in ("t", "testing"):
        next_view = ViewState(show_done=False, only_in_progress=False, only_testing=True)
        updated_tasks = _refresh_and_render(context, next_view)
        return CommandOutcome(updated_tasks, next_view)

    if command == "":
        return CommandOutcome(tasks_by_date, view_state)

    print(f"{Colors.ERROR}Unknown command. Type 'help' for available commands.{Colors.RESET}")
    return CommandOutcome(tasks_by_date, view_state)
