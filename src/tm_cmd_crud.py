"""Task CRUD command handlers: new, change state, add note, edit, delete, move, duplicate, subtask, das."""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .tm_cmd_common import (
    CommandContext,
    CommandOutcome,
    ViewState,
    Colors,
    _apply_tags_to_text,
    _confirm_action,
    _extract_inline_meta,
    _get_state_color_inline,
    _log,
    _maybe_autoclose_parent,
    _parse_meta_command,
    _refresh_and_render,
    _render,
    _render_inline_meta_text,
    _save_undo_snapshot,
    _strip_inline_tags,
    _strip_tags,
    _try_parse_date,
    clear_screen,
)
from .tm_config import DEFAULT_STATE, FINISHED_STATES
from .tm_features import compute_next_recurrence_date
from .tm_journal import (
    add_note_to_task_in_file,
    add_subtask_to_task,
    add_task_to_file,
    delete_note_in_file,
    delete_subtask_in_file,
    delete_task_in_file,
    duplicate_task_in_file,
    edit_note_in_file,
    edit_subtask_title_in_file,
    edit_task_title_in_file,
    mark_all_subtasks_done_in_file,
    move_task_to_date_in_file,
    read_journal_snapshot,
    update_dependency_references,
    update_task_metadata_in_file,
    update_subtask_state_in_file,
    update_task_state_in_file,
)
from .tm_logic import (
    find_note_by_id,
    find_task_by_id,
    normalize_priority_input,
    normalize_state_input,
    parse_date_input,
    parse_new_command_args,
)
from .tm_models import Subtask, Task


def handle_new(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: n, new — create a new task."""
    if not re.match(r"^\s*(?:n|new)\b", raw_command, re.IGNORECASE):
        return None

    task_title, task_state, target_date, due_date, priority, recurrence, parse_error = parse_new_command_args(raw_command)
    if parse_error:
        _log("error", f"{parse_error}")
        print(
            f"{Colors.DIM}Usage: n [title] [--state <state>] [--date dd/mm/yyyy] "
            f"[--due dd/mm/yyyy] [--priority <level>] [--recur <freq>]{Colors.RESET}"
        )
        return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)

    result = None
    if not task_title:
        from .tm_form import show_form, TextField, SelectField
        from .tm_config import VALID_STATES, VALID_PRIORITIES

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
        except Exception:
            import traceback
            Path("src/ttm_crash.log").write_text(traceback.format_exc(), encoding="utf-8")
            _log("error", f"Form crashed. See src/ttm_crash.log")
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
            from .tm_logic import normalize_recurrence_input
            recurrence = normalize_recurrence_input(result["Recurrence"].strip())

    if not task_title:
        _log("error", f"Task title cannot be empty.")
        return CommandOutcome(tasks_by_date, view_state)

    task_state = task_state or DEFAULT_STATE
    snapshot = read_journal_snapshot(context.journal_path)

    if add_task_to_file(context.journal_path, task_title, task_state, target_date, due_date, priority, recurrence):
        _save_undo_snapshot(context, snapshot)
        if result is not None and result.get("Note", "").strip():
            updated_tasks = context.refresh_tasks()
            created_task = None
            for tasks in updated_tasks.values():
                for t in tasks:
                    if t.title.strip() == task_title.strip():
                        if created_task is None or (t.source_line or 0) > (created_task.source_line or 0):
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


def handle_change_state(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: cs, change state."""
    if not re.match(r"^\s*(?:cs|change\s+state)\b", raw_command, re.IGNORECASE):
        return None

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
        from .tm_form import show_form, SelectField
        from .tm_config import VALID_STATES as _VS
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


def handle_add_note(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: an, add note."""
    if not re.match(r"^\s*(?:an|add\s+note)\b", raw_command, re.IGNORECASE):
        return None

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
        from .tm_form import show_form, TextField
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


def handle_edit(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: e, edit, md, meta — edit task title/metadata."""
    if not re.match(r"^\s*(?:e|edit|md|meta)\b", raw_command, re.IGNORECASE):
        return None

    updated_tasks = context.refresh_tasks()

    # ─── Check for metadata flags (--due, --priority, --tags) ─────
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

        if not has_due and not has_priority and not has_tags:
            pass  # will be handled by the form section below
        else:
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

        # Check if it's a note ID
        note_target = find_note_by_id(updated_tasks, requested_id)
        if note_target is not None:
            task, note_index, note_text = note_target
            from .tm_form import show_form, TextField
            form_fields = [TextField("Note", value=note_text)]
            try:
                result = show_form(f"Edit Note — {requested_id}", form_fields)
            except Exception:
                import traceback
                Path("src/ttm_crash.log").write_text(traceback.format_exc(), encoding="utf-8")
                clear_screen()
                _render(updated_tasks, view_state)
                _log("error", "Form crashed. See src/ttm_crash.log")
                return CommandOutcome(updated_tasks, view_state)
            if result is None:
                clear_screen()
                _render(updated_tasks, view_state)
                _log("info", "Cancelled.")
                return CommandOutcome(updated_tasks, view_state)
            new_text = result["Note"].strip()
            if not new_text:
                _log("error", "Note text cannot be empty. Use 'del' to remove.")
                return CommandOutcome(updated_tasks, view_state)
            snapshot = read_journal_snapshot(context.journal_path)
            persisted = edit_note_in_file(context.journal_path, task, note_index, new_text)
            if persisted:
                _save_undo_snapshot(context, snapshot)
                refreshed = context.refresh_tasks()
                clear_screen()
                _log("info", f"Updated note {requested_id}.")
                _render(refreshed, view_state)
                return CommandOutcome(refreshed, view_state)
            _log("error", "Could not edit note in file.")
            return CommandOutcome(updated_tasks, view_state)

        target = find_task_by_id(updated_tasks, requested_id)
        if target and not isinstance(target, Subtask):
            from .tm_form import show_form, TextField, SelectField
            from .tm_config import VALID_STATES, VALID_PRIORITIES

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
            except Exception:
                import traceback
                Path("src/ttm_crash.log").write_text(traceback.format_exc(), encoding="utf-8")
                clear_screen()
                _render(updated_tasks, view_state)
                _log("error", f"Form crashed. See src/ttm_crash.log")
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
            target.title = new_title if new_title else target.title
            target.due_date = new_due
            target.priority = new_priority
            target.state = new_state
            edit_task_title_in_file(context.journal_path, target, target.title)
            update_task_state_in_file(context.journal_path, target, new_state)
            note_text = result.get("Note", "").strip()
            if note_text:
                add_note_to_task_in_file(context.journal_path, target, note_text)
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Task {requested_id} updated.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)
        elif target and isinstance(target, Subtask):
            from .tm_form import show_form, TextField, SelectField
            from .tm_config import VALID_STATES

            state_idx = VALID_STATES.index(target.state) if target.state in VALID_STATES else 0

            form_fields = [
                TextField("Title", value=target.title),
                SelectField("State", VALID_STATES, selected=state_idx),
            ]

            try:
                result = show_form(f"Edit Subtask — {target.title[:30]}", form_fields)
            except Exception:
                import traceback
                Path("src/ttm_crash.log").write_text(traceback.format_exc(), encoding="utf-8")
                clear_screen()
                _render(updated_tasks, view_state)
                _log("error", f"Form crashed. See src/ttm_crash.log")
                return CommandOutcome(updated_tasks, view_state)
            if result is None:
                clear_screen()
                _render(updated_tasks, view_state)
                _log("info", f"Cancelled.")
                return CommandOutcome(updated_tasks, view_state)

            new_title = result["Title"].strip()
            new_state = result.get("State") or target.state

            if not new_title:
                _log("error", "Title cannot be empty.")
                return CommandOutcome(updated_tasks, view_state)

            snapshot = read_journal_snapshot(context.journal_path)
            edit_subtask_title_in_file(context.journal_path, target, new_title)
            if new_state != target.state:
                update_subtask_state_in_file(context.journal_path, target, new_state)
            _save_undo_snapshot(context, snapshot)
            refreshed = context.refresh_tasks()
            clear_screen()
            _log("info", f"Subtask {requested_id} updated.")
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)
        elif target is None:
            _log("error", f"ID {requested_id} not found.")
            return CommandOutcome(updated_tasks, view_state)
        else:
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
        # Update dependency references in other tasks (blockedby:/blocks: metadata)
        if persisted:
            old_stripped = _strip_tags(target.title)
            new_stripped = _strip_tags(new_title)
            update_dependency_references(context.journal_path, old_stripped, new_stripped)

    if persisted:
        _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        _log("info", f"Updated title for {requested_id}.")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    _log("error", f"Could not edit title in file.")
    return CommandOutcome(updated_tasks, view_state)


def handle_delete(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: del, delete."""
    if not re.match(r"^\s*(?:del|delete)\b", raw_command, re.IGNORECASE):
        return None

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


def handle_move(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: mv, move, reschedule."""
    if not re.match(r"^\s*(?:mv|move|reschedule)\b", raw_command, re.IGNORECASE):
        return None

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
        from .tm_form import show_form, TextField
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


def handle_duplicate(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: dup, duplicate."""
    if not re.match(r"^\s*(?:dup|duplicate)\b", raw_command, re.IGNORECASE):
        return None

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


def handle_subtask(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: sub — add subtask."""
    if not re.match(r"^\s*sub\b", raw_command, re.IGNORECASE):
        return None

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
        from .tm_form import show_form, TextField, SelectField
        from .tm_config import VALID_STATES as _VS, VALID_PRIORITIES as _VP
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
        due_str = result.get("Due date", "").strip()
        prio_str = result.get("Priority", "").strip()
        tags_str = result.get("Tags", "").strip()
        # Validate due date if provided
        if due_str and parse_date_input(due_str) is None:
            clear_screen()
            _render(refreshed, view_state)
            _log("error", f"Invalid due date format: {due_str}")
            return CommandOutcome(refreshed, view_state)
        # Validate priority if provided
        if prio_str:
            from .tm_config import VALID_PRIORITIES as _ALL_PRIOS
            if prio_str.upper() not in (p.upper() for p in _ALL_PRIOS):
                clear_screen()
                _render(refreshed, view_state)
                _log("error", f"Invalid priority: {prio_str}. Valid: {', '.join(_ALL_PRIOS)}")
                return CommandOutcome(refreshed, view_state)
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


def handle_done_all_subtasks(raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext) -> Optional[CommandOutcome]:
    """Handle: das, done all subtasks."""
    if not re.match(r"^\s*(?:das|done\s+all\s+subtasks)\b", raw_command, re.IGNORECASE):
        return None

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
