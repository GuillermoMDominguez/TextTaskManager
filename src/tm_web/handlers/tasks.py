"""Task CRUD API handlers — create, edit, delete tasks, subtasks, notes."""

from datetime import datetime
from pathlib import Path

from src.tm_journal import (
    add_note_to_subtask_in_file,
    add_note_to_task_in_file,
    add_subtask_to_task,
    add_task_to_file,
    delete_note_in_file,
    delete_subtask_in_file,
    delete_task_in_file,
    edit_note_in_file,
    edit_subtask_title_in_file,
    update_subtask_metadata_in_file,
    update_subtask_state_in_file,
    update_task_metadata_in_file,
    update_task_state_in_file,
    write_journal,
)
from src.tm_logic import (
    find_task_by_id,
    normalize_recurrence_input,
    normalize_state_input,
)
from src.tm_views_data import _task_to_view_item
from src.tm_models import Subtask

from ..serializers import error_response, json_response, read_body, serialize_task
from ..state import _state_proxy as _state


def api_create_task(handler, params) -> None:
    """POST /api/tasks — create a new task."""
    body = read_body(handler)
    title = body.get("title", "").strip()
    state = body.get("state", "BACKLOG")
    due_date = body.get("due_date")
    priority = body.get("priority")
    jira_key = body.get("jira_key")
    recurrence = body.get("recurrence")
    tags = body.get("tags", [])

    if not title:
        error_response(handler, "title is required")
        return

    if tags:
        tag_str = " ".join(f"#{t.lstrip('#')}" for t in tags if t.strip())
        if tag_str:
            title = f"{title} {tag_str}"

    try:
        date_obj = None
        if due_date:
            try:
                date_obj = datetime.strptime(due_date, "%d/%m/%Y")
            except ValueError:
                try:
                    date_obj = datetime.strptime(due_date, "%d/%m/%y")
                except ValueError:
                    try:
                        date_obj = datetime.strptime(due_date, "%Y-%m-%d")
                    except ValueError:
                        pass

        rec = normalize_recurrence_input(recurrence) if recurrence else None

        add_task_to_file(
            _state.journal_path,
            title=title,
            state=state,
            due_date=date_obj,
            priority=priority,
            recurrence=rec,
            jira_key=jira_key.strip().upper() if jira_key else None,
        )
        _state.refresh()
        json_response(handler, {"ok": True}, 201)
    except Exception as e:
        error_response(handler, str(e), 500)


def api_change_state(handler, params) -> None:
    """POST /api/tasks/<id>/state — change task state."""
    body = read_body(handler)
    task_id = params.get("task_id", [None])[0] or body.get("task_id")
    new_state = body.get("state", "").strip()

    if not task_id or not new_state:
        error_response(handler, "task_id and state are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    normalized = normalize_state_input(new_state)
    if not normalized:
        error_response(handler, f"Invalid state: {new_state}")
        return

    try:
        update_task_state_in_file(_state.journal_path, task, normalized)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            json_response(handler, serialize_task(_task_to_view_item(updated)))
        else:
            json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_edit_task(handler, params) -> None:
    """POST /api/tasks/<id>/edit — edit task title, priority, due date."""
    body = read_body(handler)
    task_id = params.get("task_id", [None])[0] or body.get("task_id")

    if not task_id:
        error_response(handler, "task_id is required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        new_title = body.get("title", "").strip()
        new_tags = body.get("tags") if "tags" in body else None

        if new_tags is not None:
            from src.tm_cmd_common import _apply_tags_to_text, _strip_inline_tags
            clean_tags = [t.lstrip('#') for t in new_tags if t.strip()]
            base_title = new_title if new_title else task.title
            base_title = _strip_inline_tags(base_title)
            new_title = _apply_tags_to_text(base_title, clean_tags)

        if new_title and new_title != task.title:
            from src.tm_journal import edit_task_title_in_file as _edit_title
            _edit_title(_state.journal_path, task, new_title)
            _state.refresh()
            task = find_task_by_id(_state.tasks_by_date, task_id)
            if not task:
                json_response(handler, {"ok": True})
                return

        new_state = body.get("state", "").strip()
        if new_state and new_state != task.state:
            normalized = normalize_state_input(new_state)
            if normalized:
                update_task_state_in_file(_state.journal_path, task, normalized)
                _state.refresh()
                task = find_task_by_id(_state.tasks_by_date, task_id)
                if not task:
                    json_response(handler, {"ok": True})
                    return

        new_priority = body.get("priority")
        new_due = body.get("due_date")
        new_recurrence = body.get("recurrence")
        new_jira_key = body.get("jira_key")
        new_linked_notes = body.get("linked_notes")

        due_obj = task.due_date
        if new_due is not None:
            if new_due == "" or new_due is False:
                due_obj = None
            else:
                try:
                    due_obj = datetime.strptime(new_due, "%d/%m/%Y")
                except ValueError:
                    try:
                        due_obj = datetime.strptime(new_due, "%d/%m/%y")
                    except ValueError:
                        try:
                            due_obj = datetime.strptime(new_due, "%Y-%m-%d")
                        except ValueError:
                            pass

        priority_val = new_priority if new_priority is not None else task.priority
        if priority_val == "":
            priority_val = None

        recurrence_val = None
        if new_recurrence is not None:
            recurrence_val = normalize_recurrence_input(new_recurrence) if new_recurrence.strip() else ""

        jira_key_val = None
        if new_jira_key is not None:
            jira_key_val = new_jira_key.strip().upper() if new_jira_key.strip() else ""

        notes_val = None
        if new_linked_notes is not None:
            if isinstance(new_linked_notes, list):
                notes_val = ",".join(new_linked_notes) if new_linked_notes else ""
            else:
                notes_val = str(new_linked_notes)

        if (due_obj != task.due_date or priority_val != task.priority
                or recurrence_val is not None or jira_key_val is not None or notes_val is not None):
            update_task_metadata_in_file(
                _state.journal_path, task,
                due_date=due_obj,
                priority=priority_val,
                recurrence=recurrence_val,
                jira_key=jira_key_val,
                notes=notes_val,
            )

        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            json_response(handler, serialize_task(_task_to_view_item(updated)))
        else:
            json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_delete_task(handler, params) -> None:
    """POST /api/tasks/<id>/delete — delete a task."""
    task_id = params.get("task_id", [None])[0]

    if not task_id:
        error_response(handler, "task_id is required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        delete_task_in_file(_state.journal_path, task)
        _state.refresh()
        json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_add_note(handler, params) -> None:
    """POST /api/tasks/<id>/notes — add a note to a task."""
    body = read_body(handler)
    _state.refresh()
    task_id = params.get("task_id", [None])[0]
    note = body.get("note", "").strip()

    if not task_id or not note:
        error_response(handler, "task_id and note are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        if not add_note_to_task_in_file(_state.journal_path, task, note):
            error_response(handler, "Failed to add note", 500)
            return
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            json_response(handler, serialize_task(_task_to_view_item(updated)))
        else:
            json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_delete_note(handler, params) -> None:
    """POST /api/tasks/<id>/notes/delete — delete a note from a task."""
    body = read_body(handler)
    task_id = params.get("task_id", [None])[0]
    note_index = body.get("index")

    if not task_id or note_index is None:
        error_response(handler, "task_id and index are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        delete_note_in_file(_state.journal_path, task, int(note_index))
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            json_response(handler, serialize_task(_task_to_view_item(updated)))
        else:
            json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_edit_note(handler, params) -> None:
    """POST /api/tasks/<id>/notes/edit — edit a note on a task."""
    body = read_body(handler)
    _state.refresh()
    task_id = params.get("task_id", [None])[0]
    note_index = body.get("index")
    new_note = body.get("note", "").strip()

    if not task_id or note_index is None or not new_note:
        error_response(handler, "task_id, index, and note are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        edit_note_in_file(_state.journal_path, task, int(note_index), new_note)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            json_response(handler, serialize_task(_task_to_view_item(updated)))
        else:
            json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_add_subtask(handler, params) -> None:
    """POST /api/tasks/<id>/subtasks — add a subtask to a task."""
    body = read_body(handler)
    task_id = params.get("task_id", [None])[0]
    title = body.get("title", "").strip()
    state = body.get("state", "BACKLOG")

    if not task_id or not title:
        error_response(handler, "task_id and title are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    if not hasattr(task, 'subtasks'):
        error_response(handler, "Cannot add subtask to a subtask")
        return

    try:
        add_subtask_to_task(_state.journal_path, task, title, state)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            json_response(handler, serialize_task(_task_to_view_item(updated)))
        else:
            json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_edit_subtask(handler, params) -> None:
    """POST /api/subtasks/<id>/edit — edit a subtask."""
    body = read_body(handler)
    _state.refresh()
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        error_response(handler, "subtask_id is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        new_title = body.get("title", "").strip()
        new_state = body.get("state", "").strip()
        due_date_str = body.get("due_date", "").strip() if body.get("due_date") is not None else None
        priority_str = body.get("priority", "").strip() if body.get("priority") is not None else None
        note_to_add = body.get("add_note", "").strip() if body.get("add_note") else ""
        new_tags = body.get("tags") if "tags" in body else None

        if new_tags is not None and new_title:
            from src.tm_cmd_common import _apply_tags_to_text
            clean_tags = [t.lstrip('#') for t in new_tags if t.strip()]
            new_title = _apply_tags_to_text(new_title, clean_tags)

        if new_title and new_title != subtask.title:
            edit_subtask_title_in_file(_state.journal_path, subtask, new_title)
            _state.refresh()
            subtask = find_task_by_id(_state.tasks_by_date, subtask_id)

        if new_state and subtask:
            normalized = normalize_state_input(new_state)
            if normalized and normalized != subtask.state:
                update_subtask_state_in_file(_state.journal_path, subtask, normalized)
                _state.refresh()
                subtask = find_task_by_id(_state.tasks_by_date, subtask_id)

        if subtask and (due_date_str is not None or priority_str is not None):
            new_due = None
            clear_due = False
            if due_date_str is not None:
                if due_date_str == "":
                    clear_due = True
                else:
                    try:
                        new_due = datetime.strptime(due_date_str, "%Y-%m-%d")
                    except ValueError:
                        try:
                            new_due = datetime.strptime(due_date_str, "%d/%m/%Y")
                        except ValueError:
                            try:
                                new_due = datetime.strptime(due_date_str, "%d/%m/%y")
                            except ValueError:
                                pass

            new_priority = None
            clear_priority = False
            if priority_str is not None:
                if priority_str == "":
                    clear_priority = True
                else:
                    new_priority = priority_str.upper()

            update_subtask_metadata_in_file(
                _state.journal_path, subtask,
                due_date=new_due, priority=new_priority,
                clear_due=clear_due, clear_priority=clear_priority,
            )
            _state.refresh()
            subtask = find_task_by_id(_state.tasks_by_date, subtask_id)

        new_linked_notes = body.get("linked_notes")
        if subtask and new_linked_notes is not None:
            notes_val = ""
            if isinstance(new_linked_notes, list):
                notes_val = ",".join(new_linked_notes) if new_linked_notes else ""
            elif isinstance(new_linked_notes, str):
                notes_val = new_linked_notes
            update_subtask_metadata_in_file(
                _state.journal_path, subtask,
                due_date=None, priority=None,
                notes=notes_val,
            )
            _state.refresh()
            subtask = find_task_by_id(_state.tasks_by_date, subtask_id)

        if subtask and note_to_add:
            if not add_note_to_subtask_in_file(_state.journal_path, subtask, note_to_add):
                error_response(handler, "Failed to add subtask note", 500)
                return
            _state.refresh()

        json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_delete_subtask(handler, params) -> None:
    """POST /api/subtasks/<id>/delete — delete a subtask."""
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        error_response(handler, "subtask_id is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        delete_subtask_in_file(_state.journal_path, subtask)
        _state.refresh()
        json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_add_subtask_note(handler, params) -> None:
    """POST /api/subtasks/<id>/notes — add a note to a subtask."""
    body = read_body(handler)
    _state.refresh()
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        error_response(handler, "subtask_id is required")
        return

    note = body.get("note", "").strip()
    if not note:
        error_response(handler, "note is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        if not add_note_to_subtask_in_file(_state.journal_path, subtask, note):
            error_response(handler, "Failed to add subtask note", 500)
            return
        _state.refresh()
        json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_delete_subtask_note(handler, params) -> None:
    """POST /api/subtasks/<id>/notes/delete — delete a subtask note by index."""
    body = read_body(handler)
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        error_response(handler, "subtask_id is required")
        return

    note_index = body.get("note_index")
    if note_index is None:
        error_response(handler, "note_index is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        idx = int(note_index)
        if idx < 0 or idx >= len(subtask.comments):
            error_response(handler, f"Invalid note_index {idx}", 400)
            return
        lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
        line_index = subtask.source_line - 1
        note_count = 0
        target_line = None
        for i in range(line_index + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith(":"):
                if note_count == idx:
                    target_line = i
                    break
                note_count += 1
            elif stripped.startswith("+") or stripped.startswith("-") or not stripped:
                break
        if target_line is not None:
            del lines[target_line]
            write_journal(_state.journal_path, "\n".join(lines))
            _state.refresh()
            json_response(handler, {"ok": True})
        else:
            error_response(handler, "Note line not found in file", 404)
    except Exception as e:
        error_response(handler, str(e), 500)


def api_edit_subtask_note(handler, params) -> None:
    """POST /api/subtasks/<id>/notes/edit — edit a subtask note by index."""
    body = read_body(handler)
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        error_response(handler, "subtask_id is required")
        return

    note_index = body.get("note_index")
    new_note = body.get("note", "").strip()
    if note_index is None:
        error_response(handler, "note_index is required")
        return
    if not new_note:
        error_response(handler, "note is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        idx = int(note_index)
        if idx < 0 or idx >= len(subtask.comments):
            error_response(handler, f"Invalid note_index {idx}", 400)
            return
        lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
        line_index = subtask.source_line - 1
        note_count = 0
        target_line = None
        for i in range(line_index + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith(":"):
                if note_count == idx:
                    target_line = i
                    break
                note_count += 1
            elif stripped.startswith("+") or stripped.startswith("-") or not stripped:
                break
        if target_line is not None:
            indent = ""
            orig = lines[target_line]
            for ch in orig:
                if ch in (" ", "\t"):
                    indent += ch
                else:
                    break
            lines[target_line] = f"{indent}: {new_note}\n"
            write_journal(_state.journal_path, "\n".join(lines))
            _state.refresh()
            json_response(handler, {"ok": True})
        else:
            error_response(handler, "Note line not found in file", 404)
    except Exception as e:
        error_response(handler, str(e), 500)


def api_archive_tasks(handler, params) -> None:
    """POST /api/tasks/archive — archive finished tasks (optionally before a date)."""
    from src.tm_journal import archive_finished_tasks_in_file, read_journal_snapshot
    from src.tm_cmd_common import _try_parse_date, _default_archive_path
    body = read_body(handler)
    before_date = body.get("before_date", "").strip()
    parsed = _try_parse_date(before_date) if before_date else None
    snapshot = read_journal_snapshot(_state.journal_path)
    archive_path = _default_archive_path(_state.journal_path)
    moved = archive_finished_tasks_in_file(_state.journal_path, archive_path, parsed)
    _state.refresh()
    json_response(handler, {"ok": True, "archived": moved, "archive_path": str(archive_path)})


def api_batch_state(handler, params) -> None:
    """POST /api/tasks/batch/state — change state for multiple task IDs."""
    body = read_body(handler)
    task_ids = body.get("task_ids", [])
    new_state = body.get("state", "").strip()
    if not task_ids or not new_state:
        error_response(handler, "task_ids and state are required")
        return
    from src.tm_journal import update_task_state_in_file
    success = 0
    for tid in task_ids:
        task = find_task_by_id(_state.tasks_by_date, tid)
        if task and not isinstance(task, Subtask):
            if update_task_state_in_file(_state.journal_path, task, new_state):
                success += 1
    _state.refresh()
    json_response(handler, {"ok": True, "updated": success})


def api_batch_tags(handler, params) -> None:
    """POST /api/tasks/batch/tags — add/remove tags for multiple task IDs."""
    body = read_body(handler)
    task_ids = body.get("task_ids", [])
    add_tags = body.get("add_tags", [])
    remove_tags = body.get("remove_tags", [])
    if not task_ids or (not add_tags and not remove_tags):
        error_response(handler, "task_ids and at least one tag operation required")
        return
    from src.tm_journal import read_journal_snapshot, write_journal
    from src.tm_views_data import get_all_tasks_flat
    snapshot = read_journal_snapshot(_state.journal_path)
    lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
    all_tasks = get_all_tasks_flat(_state.tasks_by_date)
    updated = 0
    for tid in task_ids:
        task = find_task_by_id(_state.tasks_by_date, tid)
        if not task or isinstance(task, Subtask) or not task.source_line:
            continue
        idx = task.source_line - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        tag_set = set(re.findall(r"#([\w-]+)", line))
        changed = False
        for t in add_tags:
            if t not in tag_set:
                line = line.rstrip("\n") + f" #{t}\n"
                changed = True
                tag_set.add(t)
        if remove_tags:
            for t in remove_tags:
                if t in tag_set:
                    line = re.sub(rf" #{re.escape(t)}\b", "", line)
                    changed = True
        if changed:
            lines[idx] = line
            updated += 1
    write_journal(_state.journal_path, "\n".join(lines))
    _state.refresh()
    json_response(handler, {"ok": True, "updated": updated})


def api_batch_priority(handler, params) -> None:
    """POST /api/tasks/batch/priority — change priority for multiple task IDs."""
    body = read_body(handler)
    task_ids = body.get("task_ids", [])
    priority = body.get("priority", "").strip()
    if not task_ids or not priority:
        error_response(handler, "task_ids and priority are required")
        return
    from src.tm_config import VALID_PRIORITIES
    if priority.upper() not in VALID_PRIORITIES:
        error_response(handler, f"Invalid priority: {priority}")
        return
    from src.tm_journal import update_task_metadata_in_file
    success = 0
    for tid in task_ids:
        task = find_task_by_id(_state.tasks_by_date, tid)
        if task and not isinstance(task, Subtask):
            if update_task_metadata_in_file(_state.journal_path, task, task.due_date, priority):
                success += 1
    _state.refresh()
    json_response(handler, {"ok": True, "updated": success})


def api_batch_delete(handler, params) -> None:
    """POST /api/tasks/batch/delete — delete multiple tasks by ID."""
    body = read_body(handler)
    task_ids = body.get("task_ids", [])
    if not task_ids:
        error_response(handler, "task_ids is required")
        return
    from src.tm_journal import delete_task_in_file, read_journal_snapshot
    snapshot = read_journal_snapshot(_state.journal_path)
    success = 0
    for tid in task_ids:
        task = find_task_by_id(_state.tasks_by_date, tid)
        if task and not isinstance(task, Subtask):
            if delete_task_in_file(_state.journal_path, task):
                success += 1
    _state.refresh()
    json_response(handler, {"ok": True, "deleted": success})


def api_import_tasks(handler, params) -> None:
    """POST /api/tasks/import — import tasks from uploaded JSON."""
    body = read_body(handler)
    json_text = body.get("json", "")
    if not json_text:
        error_response(handler, "json field is required")
        return
    from src.tm_features import import_from_json
    from src.tm_journal import read_journal_snapshot, write_journal
    new_lines = import_from_json(json_text)
    if not new_lines:
        error_response(handler, "Could not parse JSON or empty content")
        return
    snapshot = read_journal_snapshot(_state.journal_path)
    from src.tm_journal import file_lock
    with file_lock:
        existing = _state.journal_path.read_text(encoding="utf-8")
        write_journal(_state.journal_path, existing + "".join(new_lines))
    _state.refresh()
    task_count = sum(1 for line in new_lines if line.strip().startswith("-"))
    json_response(handler, {"ok": True, "imported": task_count})
