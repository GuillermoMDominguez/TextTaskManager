"""CLI command handlers for notes (nn, notes, vn, en, dn, mn, ln, uln)."""

import re
from typing import Optional

from .tm_cmd_common import (
    CommandContext,
    CommandOutcome,
    ViewState,
    _log,
    _refresh_and_render,
    _save_undo_snapshot,
    clear_screen,
    read_journal_snapshot,
)
from .tm_logic import find_task_by_id
from .tm_models import Task, Subtask
from .tm_notes import (
    create_note_interactive,
    read_note,
    edit_note_interactive,
    delete_note,
    move_note,
    list_notes,
    resolve_note_path,
    link_note_to_task,
    unlink_note_from_task,
)
from .tm_ui import Colors


def _find_task(raw_id: str, updated_tasks: dict):
    target = find_task_by_id(updated_tasks, raw_id)
    if target is None:
        _log("error", f"Task ID {raw_id} not found.")
        return None
    if isinstance(target, Subtask):
        _log("error", "Subtask IDs are not supported for note linking.")
        return None
    return target


def handle_new_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: nn, new note — create a new .md note via editor."""
    if not re.match(r"^\s*(?:nn|new\s+note)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*(?:nn|new\s+note)\s+(.+)\s*$", raw_command, re.IGNORECASE)

    if match:
        title = match.group(1).strip()
    else:
        from .tm_form import show_form, TextField
        result = show_form("New Note", [TextField("Title", placeholder="path/Note title")])
        if result is None:
            return CommandOutcome(updated_tasks, view_state, skip_redraw=True)
        title = result["Title"].strip()

    if not title:
        _log("error", "Note title cannot be empty.")
        return CommandOutcome(updated_tasks, view_state)

    rel = create_note_interactive(context.journal_path, title)
    if rel is None:
        return CommandOutcome(updated_tasks, view_state)

    clear_screen()
    _log("info", f"Note created: {rel}")
    return CommandOutcome(updated_tasks, view_state)


def handle_list_notes(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: notes — list all notes, optionally filtered by folder."""
    if not re.match(r"^\s*notes\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*notes\s+(.+)\s*$", raw_command, re.IGNORECASE)
    folder = match.group(1).strip() if match else None

    notes = list_notes(context.journal_path, folder)

    clear_screen()
    if not notes:
        _log("info", "No notes found.")
        return CommandOutcome(updated_tasks, view_state, skip_redraw=True)

    print(f"\n{Colors.HEADER}{Colors.BOLD}Notes{' in ' + folder if folder else ''}:{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 60}{Colors.RESET}")
    for n in notes:
        preview = n["preview"][:55] if n["preview"] else "(empty)"
        print(f"  {Colors.BOLD}{n['path']}{Colors.RESET}")
        print(f"    {Colors.DIM}{preview}{Colors.RESET}")
    print()
    return CommandOutcome(updated_tasks, view_state, skip_redraw=True)


def handle_view_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: vn, view note — display note content."""
    if not re.match(r"^\s*(?:vn|view\s+note)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*(?:vn|view\s+note)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", "Usage: vn <note_path>")
        return CommandOutcome(updated_tasks, view_state)

    note_path_str = match.group(1).strip()
    content = read_note(context.journal_path, note_path_str)
    if content is None:
        _log("error", f"Note not found: {note_path_str}")
        return CommandOutcome(updated_tasks, view_state)

    clear_screen()
    print(f"\n{Colors.HEADER}{Colors.BOLD}{note_path_str}{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 60}{Colors.RESET}")
    print(content)
    if not content.endswith("\n"):
        print()
    return CommandOutcome(updated_tasks, view_state, skip_redraw=True)


def handle_edit_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: en, edit note — open note in editor."""
    if not re.match(r"^\s*(?:en|edit\s+note)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*(?:en|edit\s+note)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", "Usage: en <note_path>")
        return CommandOutcome(updated_tasks, view_state)

    note_path_str = match.group(1).strip()
    if edit_note_interactive(context.journal_path, note_path_str):
        return CommandOutcome(updated_tasks, view_state)
    return CommandOutcome(updated_tasks, view_state)


def handle_delete_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: dn, del note — delete a note."""
    if not re.match(r"^\s*(?:dn|del\s+note)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*(?:dn|del\s+note)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", "Usage: dn <note_path>")
        return CommandOutcome(updated_tasks, view_state)

    note_path_str = match.group(1).strip()
    resolved = resolve_note_path(context.journal_path, note_path_str)
    if resolved is None:
        _log("error", f"Note not found: {note_path_str}")
        return CommandOutcome(updated_tasks, view_state)

    # Ask confirmation
    print(f"\n{Colors.WARNING}Delete note '{note_path_str}'? (y/N):{Colors.RESET} ", end="", flush=True)
    answer = input().strip().lower()
    if answer != "y":
        clear_screen()
        _log("info", "Cancelled.")
        return CommandOutcome(updated_tasks, view_state)

    if delete_note(context.journal_path, note_path_str):
        clear_screen()
        _log("info", f"Note deleted: {note_path_str}")
        return CommandOutcome(updated_tasks, view_state)

    _log("error", f"Could not delete note: {note_path_str}")
    return CommandOutcome(updated_tasks, view_state)


def handle_move_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: mn — move/rename a note."""
    if not re.match(r"^\s*mn\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*mn\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", "Usage: mn <from_path> <to_path>")
        return CommandOutcome(updated_tasks, view_state)

    from_path = match.group(1).strip()
    to_path = match.group(2).strip()

    if move_note(context.journal_path, from_path, to_path):
        clear_screen()
        _log("info", f"Moved: {from_path} -> {to_path}")
        return CommandOutcome(updated_tasks, view_state)

    _log("error", f"Could not move note: {from_path}")
    return CommandOutcome(updated_tasks, view_state)


def handle_link_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: ln, link note — link a note to a task. Creates note if not exists."""
    if not re.match(r"^\s*(?:ln|link\s+note)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*(?:ln|link\s+note)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", "Usage: ln <task_id> <note_path_or_title>")
        return CommandOutcome(updated_tasks, view_state)

    raw_id = match.group(1).strip()
    note_ref = match.group(2).strip()

    target = _find_task(raw_id, updated_tasks)
    if target is None:
        return CommandOutcome(updated_tasks, view_state)

    # Check if note exists; if not, create it interactively
    resolved = resolve_note_path(context.journal_path, note_ref)
    if resolved is None:
        _log("info", f"Note '{note_ref}' not found. Creating it first...")
        rel = create_note_interactive(context.journal_path, note_ref)
        if rel is None:
            return CommandOutcome(updated_tasks, view_state)
        note_ref = rel
    else:
        note_ref = str(resolved.relative_to(resolved.parent.parent / "notes"))

    snapshot = read_journal_snapshot(context.journal_path)
    if link_note_to_task(context.journal_path, target, note_ref):
        _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        _log("info", f"Note '{note_ref}' linked to task {raw_id}.")
        return CommandOutcome(refreshed, view_state)

    _log("error", "Could not link note.")
    return CommandOutcome(updated_tasks, view_state)


def handle_unlink_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: uln, unlink note — unlink a note from a task."""
    if not re.match(r"^\s*(?:uln|unlink\s+note)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()
    match = re.match(r"^\s*(?:uln|unlink\s+note)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", "Usage: uln <task_id> <note_path>")
        return CommandOutcome(updated_tasks, view_state)

    raw_id = match.group(1).strip()
    note_ref = match.group(2).strip()

    target = _find_task(raw_id, updated_tasks)
    if target is None:
        return CommandOutcome(updated_tasks, view_state)

    snapshot = read_journal_snapshot(context.journal_path)
    if unlink_note_from_task(context.journal_path, target, note_ref):
        _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        _log("info", f"Note '{note_ref}' unlinked from task {raw_id}.")
        return CommandOutcome(refreshed, view_state)

    _log("error", "Could not unlink note.")
    return CommandOutcome(updated_tasks, view_state)
