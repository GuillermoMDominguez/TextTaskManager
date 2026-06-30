"""Standalone .md note API handlers."""

from pathlib import Path

from src.tm_notes import (
    delete_note,
    list_note_folders,
    list_notes,
    move_note,
    read_note,
    resolve_or_create_note_path,
    write_note,
)

from ..serializers import error_response, json_response
from ..state import _state_proxy as _state


def api_get_notes(handler, params) -> None:
    """GET /api/notes — list notes, optionally filtered by folder."""
    folder = params.get("folder", [None])[0]
    notes = list_notes(_state.journal_path, folder)
    json_response(handler, {"notes": notes})


def api_get_note_folders(handler, params) -> None:
    """GET /api/notes/folders — list available note folders."""
    folders = list_note_folders(_state.journal_path)
    json_response(handler, {"folders": folders})


def api_get_note(handler, params) -> None:
    """GET /api/notes/<name> — get note content."""
    note_name = params.get("name", [None])[0]
    if not note_name:
        error_response(handler, "name is required")
        return
    content = read_note(_state.journal_path, note_name)
    if content is None:
        error_response(handler, f"Note '{note_name}' not found", 404)
        return
    json_response(handler, {"path": note_name, "content": content})


def api_create_note(handler, params) -> None:
    """POST /api/notes — create a new note."""
    from ..serializers import read_body

    body = read_body(handler)
    name = body.get("name", "").strip()
    content = body.get("content", "")

    if not name:
        error_response(handler, "name is required")
        return

    resolved = resolve_or_create_note_path(_state.journal_path, name)
    if not name.endswith(".md"):
        name = name + ".md"
    if write_note(_state.journal_path, name, content):
        json_response(handler, {"path": name, "ok": True})
    else:
        error_response(handler, "Could not write note", 500)


def api_delete_note_route(handler, params) -> None:
    """POST /api/notes/delete — delete a note."""
    from ..serializers import read_body

    body = read_body(handler)
    name = (params.get("name", [None])[0] or body.get("name", "")).strip()
    if not name:
        error_response(handler, "name is required")
        return
    if delete_note(_state.journal_path, name):
        json_response(handler, {"ok": True})
    else:
        error_response(handler, f"Note '{name}' not found", 404)


def api_move_note_route(handler, params) -> None:
    """POST /api/notes/move — move or rename a note."""
    from ..serializers import read_body

    body = read_body(handler)
    from_path = body.get("from", "").strip()
    to_path = body.get("to", "").strip()
    if not from_path or not to_path:
        error_response(handler, "from and to are required")
        return
    if move_note(_state.journal_path, from_path, to_path):
        json_response(handler, {"ok": True, "from": from_path, "to": to_path})
    else:
        error_response(handler, f"Could not move note from '{from_path}' to '{to_path}'", 500)


def api_link_task_note(handler, params) -> None:
    """POST /api/tasks/notes/link — link a note to a task."""
    from src.tm_logic import find_task_by_id
    from src.tm_notes import resolve_note_path, write_note

    from ..serializers import read_body, serialize_task
    from ..state import _state_proxy as _state

    body = read_body(handler)
    _state.refresh()
    task_id = params.get("task_id", [None])[0] or body.get("task_id", "").strip()
    note_path = body.get("note_path", "").strip() or body.get("name", "").strip()

    if not task_id or not note_path:
        error_response(handler, "task_id and note_path/name are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    # Ensure note exists — create if missing
    resolved = resolve_note_path(_state.journal_path, note_path)
    if resolved is None:
        write_note(_state.journal_path, note_path, f"# {Path(note_path).stem}\n\n")

    # Append to linked notes
    current = list(task.linked_notes or [])
    if note_path not in current:
        current.append(note_path)
    from src.tm_journal import update_task_metadata_in_file
    update_task_metadata_in_file(
        _state.journal_path, task,
        due_date=task.due_date,
        priority=task.priority,
        recurrence=task.recurrence,
        jira_key=task.jira_key,
        notes=",".join(current),
    )
    _state.refresh()
    updated = find_task_by_id(_state.tasks_by_date, task_id)
    if updated:
        from src.tm_views_data import _task_to_view_item
        json_response(handler, serialize_task(_task_to_view_item(updated)))
    else:
        json_response(handler, {"ok": True})


def api_unlink_task_note(handler, params) -> None:
    """POST /api/tasks/notes/unlink — unlink a note from a task."""
    from src.tm_logic import find_task_by_id
    from src.tm_journal import update_task_metadata_in_file
    from src.tm_views_data import _task_to_view_item

    from ..serializers import read_body, serialize_task
    from ..state import _state_proxy as _state

    body = read_body(handler)
    _state.refresh()
    task_id = params.get("task_id", [None])[0] or body.get("task_id", "").strip()
    note_path = body.get("note_path", "").strip() or body.get("name", "").strip()

    if not task_id or not note_path:
        error_response(handler, "task_id and note_path are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    current = [n for n in (task.linked_notes or []) if n != note_path]
    notes_val = ",".join(current) if current else ""
    update_task_metadata_in_file(
        _state.journal_path, task,
        due_date=task.due_date,
        priority=task.priority,
        recurrence=task.recurrence,
        jira_key=task.jira_key,
        notes=notes_val,
    )
    _state.refresh()
    updated = find_task_by_id(_state.tasks_by_date, task_id)
    if updated:
        json_response(handler, serialize_task(_task_to_view_item(updated)))
    else:
        json_response(handler, {"ok": True})
