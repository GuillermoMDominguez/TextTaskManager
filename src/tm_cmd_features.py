"""Extended feature command handlers: archive, templates, recurrence, time tracking,
blockers, pomodoro, burndown, kanban, project/tag views, export, import, weekly report,
sort, and email."""

import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .tm_cmd_common import (
    CommandContext,
    CommandOutcome,
    ViewState,
    Colors,
    _confirm_action,
    _default_archive_path,
    _get_state_color_inline,
    _log,
    _log_time_to_task,
    _refresh_and_render,
    _render,
    _save_undo_snapshot,
    _strip_tags,
    _title_without_tags_cmd,
    _try_parse_date,
    clear_screen,
)
from .tm_config import DEFAULT_STATE, VALID_RECURRENCES, RECURRENCE_ALIASES
from .tm_features import (
    add_blocker_metadata,
    add_blocks_metadata,
    compute_next_recurrence_date,
    export_to_csv,
    export_to_json,
    export_to_markdown,
    format_time_spent,
    generate_burndown,
    generate_weekly_report,
    get_all_tags,
    get_tasks_by_tag,
    get_template,
    get_templates,
    import_from_json,
    parse_time_spent,
    render_kanban,
    run_pomodoro,
    save_template,
    delete_template,
    sort_tasks,
)
from .tm_journal import (
    add_subtask_to_task,
    add_task_to_file,
    archive_finished_tasks_in_file,
    read_journal_snapshot,
    write_journal,
)
from .tm_logic import find_task_by_id, normalize_priority_input, normalize_state_input
from .tm_models import Subtask
from .tm_settings import get_setting


def handle_archive(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'ar|archive' command."""
    if not re.match(r"^\s*(?:ar|archive)\b", raw_command, re.IGNORECASE):
        return None

    match = re.match(r"^\s*(?:ar|archive)(?:\s+(\d{1,2}/\d{1,2}/\d{4}))?\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", f"Usage: ar [dd/mm/yyyy]")
        return CommandOutcome(tasks_by_date, view_state)

    before_date = _try_parse_date(match.group(1)) if match.group(1) else None
    if match.group(1) and before_date is None:
        _log("error", f"Invalid date. Use dd/mm/yyyy.")
        return CommandOutcome(tasks_by_date, view_state)

    date_label = before_date.strftime('%d/%m/%Y') if before_date else 'all dates'
    if not _confirm_action(f"Archive finished tasks up to {date_label}?"):
        _log("info", f"Archive cancelled.")
        return CommandOutcome(tasks_by_date, view_state)

    archive_path = _default_archive_path(context.journal_path)
    snapshot = read_journal_snapshot(context.journal_path)
    moved = archive_finished_tasks_in_file(context.journal_path, archive_path, before_date)
    if moved > 0:
        _save_undo_snapshot(context, snapshot)
    refreshed = context.refresh_tasks()
    clear_screen()
    _log("info", f"Archived {moved} finished task(s) to {archive_path}.")
    _render(refreshed, view_state)
    return CommandOutcome(refreshed, view_state)


def handle_template(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'tpl|template' command."""
    if not re.match(r"^\s*(?:tpl|template)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:tpl|template)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
    arg = match.group(1).strip() if match and match.group(1) else None

    if not arg:
        # List templates
        templates = get_templates()
        if not templates:
            _log("info", f"No templates saved. Use 'tpl save <name>' after creating a task to save it as template.")
            return CommandOutcome(refreshed, view_state)
        print(f"\n{Colors.HEADER}{Colors.BOLD}Templates{Colors.RESET}")
        for name, data in templates.items():
            subtask_count = len(data.get("subtasks", []))
            extra = []
            if data.get("state"):
                extra.append(data["state"])
            if data.get("priority"):
                extra.append(data["priority"])
            if subtask_count:
                extra.append(f"{subtask_count} subtasks")
            suffix = f" ({', '.join(extra)})" if extra else ""
            print(f"  {Colors.BOLD}{name}{Colors.RESET}: {data.get('title', '?')}{suffix}")
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # tpl save <name> — save last created task as template
    save_match = re.match(r"^save\s+(\S+)$", arg, re.IGNORECASE)
    if save_match:
        tpl_name = save_match.group(1)
        from .tm_form import show_form, TextField, SelectField
        from .tm_config import VALID_STATES as _VS, VALID_PRIORITIES as _VP
        form_fields = [
            TextField("Title", placeholder="Template title (required)"),
            SelectField("State", _VS, selected=_VS.index(DEFAULT_STATE) if DEFAULT_STATE in _VS else 0),
            SelectField("Priority", _VP, allow_empty=True),
            TextField("Subtasks", placeholder="sub1, sub2, sub3 (comma-separated)"),
        ]
        result = show_form(f"Save Template — {tpl_name}", form_fields)
        if result is None:
            clear_screen()
            _render(refreshed, view_state)
            _log("info", f"Cancelled.")
            return CommandOutcome(refreshed, view_state)

        title = result["Title"].strip()
        if not title:
            clear_screen()
            _render(refreshed, view_state)
            _log("error", f"Title cannot be empty.")
            return CommandOutcome(refreshed, view_state)

        template_data = {"title": title}
        state_val = result.get("State", "").strip()
        if state_val:
            normalized_state = normalize_state_input(state_val)
            if normalized_state:
                template_data["state"] = normalized_state
        priority_val = result.get("Priority", "").strip()
        if priority_val:
            normalized_priority = normalize_priority_input(priority_val)
            if normalized_priority:
                template_data["priority"] = normalized_priority
        subtasks_input = result.get("Subtasks", "").strip()
        subtasks = [s.strip() for s in subtasks_input.split(",") if s.strip()] if subtasks_input else []
        if subtasks:
            template_data["subtasks"] = subtasks

        if save_template(tpl_name, template_data):
            clear_screen()
            _render(refreshed, view_state)
            _log("info", f"Template '{tpl_name}' saved.")
        else:
            _log("error", f"Could not save template.")
        return CommandOutcome(refreshed, view_state)

    # tpl del <name>
    del_match = re.match(r"^(?:del|delete|rm)\s+(\S+)$", arg, re.IGNORECASE)
    if del_match:
        tpl_name = del_match.group(1)
        if delete_template(tpl_name):
            _log("info", f"Template '{tpl_name}' deleted.")
        else:
            _log("error", f"Template '{tpl_name}' not found.")
        return CommandOutcome(refreshed, view_state)

    # tpl <name> — use template to create task
    tpl_data = get_template(arg)
    if not tpl_data:
        _log("error", f"Template '{arg}' not found. Use 'tpl' to list.")
        return CommandOutcome(refreshed, view_state)

    tpl_title = tpl_data.get("title", arg)
    tpl_state = tpl_data.get("state", DEFAULT_STATE)
    tpl_priority = tpl_data.get("priority")
    tpl_recurrence = tpl_data.get("recurrence")
    snapshot = read_journal_snapshot(context.journal_path)

    if add_task_to_file(context.journal_path, tpl_title, tpl_state, None, None, tpl_priority, tpl_recurrence):
        _save_undo_snapshot(context, snapshot)
        # Add subtasks if any
        tpl_subtasks = tpl_data.get("subtasks", [])
        if tpl_subtasks:
            refreshed_for_sub = context.refresh_tasks()
            parent = None
            for tasks in refreshed_for_sub.values():
                for t in tasks:
                    if _strip_tags(t.title) == tpl_title:
                        parent = t
                        break
                if parent:
                    break
            if parent:
                for sub_title in tpl_subtasks:
                    add_subtask_to_task(context.journal_path, parent, sub_title, DEFAULT_STATE)

        updated_tasks = context.refresh_tasks()
        clear_screen()
        _log("info", f"Task created from template '{arg}'.")
        _render(updated_tasks, view_state)
        return CommandOutcome(updated_tasks, view_state)

    _log("error", f"Could not create task from template.")
    return CommandOutcome(refreshed, view_state)


def handle_recurrence(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'recur|rec' command."""
    recur_match = re.match(r"^\s*(?:recur|rec)\s+(\S+)\s*(.*)$", raw_command, re.IGNORECASE)
    if not recur_match:
        return None

    requested_id = recur_match.group(1).strip()
    recur_value = recur_match.group(2).strip().lower()

    target = find_task_by_id(tasks_by_date, requested_id)
    if target is None or isinstance(target, Subtask):
        _log("error", f"Task {requested_id} not found.")
        return CommandOutcome(tasks_by_date, view_state)

    if recur_value in ("none", "off", "clear", ""):
        new_recurrence = ""  # empty string = remove recurrence
    else:
        # Resolve aliases
        normalized = RECURRENCE_ALIASES.get(recur_value.upper(), recur_value)
        if normalized not in VALID_RECURRENCES:
            _log("error", f"Invalid recurrence. Valid: {', '.join(VALID_RECURRENCES)} (or none).")
            return CommandOutcome(tasks_by_date, view_state)
        new_recurrence = normalized

    from .tm_journal import update_task_metadata_in_file
    snapshot = read_journal_snapshot(context.journal_path)
    if update_task_metadata_in_file(
        context.journal_path, target, target.due_date, target.priority,
        recurrence=new_recurrence
    ):
        _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        label = new_recurrence or "none"
        _log("info", f"Task {requested_id} recurrence set to {label}.")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)

    _log("error", f"Could not update recurrence for task {requested_id}.")
    return CommandOutcome(updated_tasks, view_state)


def handle_time_tracking(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'tt|time' command."""
    if not re.match(r"^\s*(?:tt|time)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:tt|time)\s+(\S+)\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", f"Usage: tt <id> <time|start|stop>")
        return CommandOutcome(refreshed, view_state)

    task_id = match.group(1)
    time_arg = match.group(2).strip().lower()
    target = find_task_by_id(refreshed, task_id)
    if not target or isinstance(target, Subtask):
        _log("error", f"Task {task_id} not found (must be parent task).")
        return CommandOutcome(refreshed, view_state)

    if time_arg == "start":
        # Store start timestamp in memory (session only)
        if not hasattr(context, '_time_tracking'):
            context._time_tracking = {}
        context._time_tracking[task_id] = time.time()
        _log("info", f"Timer started for task {task_id}.")
        return CommandOutcome(refreshed, view_state)

    if time_arg == "stop":
        if not hasattr(context, '_time_tracking') or task_id not in context._time_tracking:
            _log("error", f"No timer running for task {task_id}. Use 'tt {task_id} start' first.")
            return CommandOutcome(refreshed, view_state)
        elapsed = time.time() - context._time_tracking.pop(task_id)
        elapsed_minutes = max(1, int(elapsed / 60 + 0.5))
        time_arg = format_time_spent(elapsed_minutes)
        _log("info", f"Timer stopped: {time_arg} elapsed.")

    # Parse and add time
    new_minutes = parse_time_spent(time_arg)
    if new_minutes is None:
        _log("error", f"Invalid time: {time_arg}. Use format like 2h, 30m, 1h30m.")
        return CommandOutcome(refreshed, view_state)

    # Update the journal line
    snapshot = read_journal_snapshot(context.journal_path)
    existing_time = target.time_spent or 0
    if _log_time_to_task(context, target, new_minutes):
        _save_undo_snapshot(context, snapshot)
        _log("info", f"Logged {format_time_spent(new_minutes)} to task {task_id} (total: {format_time_spent(existing_time + new_minutes)}).")
    else:
        _log("error", f"Could not update time in journal.")

    updated_tasks = context.refresh_tasks()
    return CommandOutcome(updated_tasks, view_state)


def handle_block_del(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'block del <blocked_id> <blocker_id>' command."""
    if not re.match(r"^\s*(?:block|blocker)\s+del\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:block|blocker)\s+del\s+(\S+)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", f"Usage: block del <blocked_id> <blocker_id>")
        return CommandOutcome(refreshed, view_state)

    blocked_id = match.group(1)
    blocker_id = match.group(2)

    blocked_task = find_task_by_id(refreshed, blocked_id)
    blocker_task = find_task_by_id(refreshed, blocker_id)

    if not blocked_task or isinstance(blocked_task, Subtask):
        _log("error", f"Task {blocked_id} not found.")
        return CommandOutcome(refreshed, view_state)
    if not blocker_task or isinstance(blocker_task, Subtask):
        _log("error", f"Task {blocker_id} not found.")
        return CommandOutcome(refreshed, view_state)

    from .tm_features import remove_blocker_metadata, remove_blocks_metadata

    snapshot = read_journal_snapshot(context.journal_path)
    lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
    updated = False

    # Remove blockedby: from the blocked task
    if blocked_task.source_line:
        idx = blocked_task.source_line - 1
        if 0 <= idx < len(lines):
            lines[idx] = remove_blocker_metadata(lines[idx], _strip_tags(blocker_task.title))
            updated = True

    # Remove blocks: from the blocker task
    if updated and blocker_task.source_line:
        idx = blocker_task.source_line - 1
        if 0 <= idx < len(lines):
            lines[idx] = remove_blocks_metadata(lines[idx], _strip_tags(blocked_task.title))

    if updated:
        write_journal(context.journal_path, "\n".join(lines))
        _save_undo_snapshot(context, snapshot)
        _log("info", f"Removed blocker: {blocker_id} no longer blocks {blocked_id}.")
        clear_screen()
        refreshed = context.refresh_tasks()
        _render(refreshed, view_state)
    else:
        _log("error", f"Could not remove blocker.")

    return CommandOutcome(context.refresh_tasks(), view_state)


def handle_unblock(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'unblock' command."""
    if not re.match(r"^\s*unblock\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*unblock\s+(\S+)\s*$", raw_command, re.IGNORECASE)

    if not match:
        # No ID given — show interactive list of blocked tasks
        from .tm_features import extract_blockers_from_line

        lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
        blocked_tasks = []
        for tasks in refreshed.values():
            for task in tasks:
                if task.source_line:
                    idx = task.source_line - 1
                    if 0 <= idx < len(lines):
                        blockers = extract_blockers_from_line(lines[idx])
                        if blockers:
                            blocked_tasks.append((task, blockers))

        if not blocked_tasks:
            _log("info", f"No blocked tasks found.")
            return CommandOutcome(refreshed, view_state)

        # Show list picker (vertical, multi-select)
        from .tm_form import show_list_picker
        import shutil as _shutil
        _cols = _shutil.get_terminal_size().columns
        # Max text width: terminal - 14 (borders, indicator, checkbox, padding)
        _max_opt = max(20, _cols - 14)
        options = []
        for t, b in blocked_tasks:
            label = f"[{t.task_id}] {_strip_tags(t.title)} (← {', '.join(b)})"
            if len(label) > _max_opt:
                label = label[:_max_opt - 1] + "…"
            options.append(label)

        selected_indices = show_list_picker("Unblock — select tasks", options, multi=True)
        if not selected_indices:
            clear_screen()
            _render(refreshed, view_state)
            return CommandOutcome(refreshed, view_state)

        # Process all selected tasks
        from .tm_features import (
            extract_blockers_from_line as _extract_blockers,
            remove_all_blocker_metadata,
            remove_blocks_metadata,
            find_task_by_title_match,
        )
        snapshot = read_journal_snapshot(context.journal_path)
        lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
        total_removed = 0

        for sel_idx in selected_indices:
            target, _ = blocked_tasks[sel_idx]
            if not target.source_line:
                continue
            idx = target.source_line - 1
            if idx < 0 or idx >= len(lines):
                continue
            blockers = _extract_blockers(lines[idx])
            if not blockers:
                continue
            # Remove all blockedby: from this task
            lines[idx] = remove_all_blocker_metadata(lines[idx])
            # Remove corresponding blocks: from each blocker task
            for blocker_title in blockers:
                blocker_task = find_task_by_title_match(refreshed, blocker_title)
                if blocker_task and blocker_task.source_line:
                    b_idx = blocker_task.source_line - 1
                    if 0 <= b_idx < len(lines):
                        lines[b_idx] = remove_blocks_metadata(lines[b_idx], _strip_tags(target.title))
            total_removed += len(blockers)

        write_journal(context.journal_path, "\n".join(lines))
        _save_undo_snapshot(context, snapshot)
        _log("info", f"Removed {total_removed} blocker(s) from {len(selected_indices)} task(s).")
        clear_screen()
        refreshed = context.refresh_tasks()
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)
    else:
        task_id = match.group(1)
        target = find_task_by_id(refreshed, task_id)

    if not target or isinstance(target, Subtask):
        _log("error", f"Task {task_id} not found.")
        return CommandOutcome(refreshed, view_state)

    from .tm_features import (
        extract_blockers_from_line, remove_all_blocker_metadata,
        remove_blocks_metadata, find_task_by_title_match,
    )

    snapshot = read_journal_snapshot(context.journal_path)
    lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")

    if not target.source_line:
        _log("error", f"Could not locate task in file.")
        return CommandOutcome(refreshed, view_state)

    idx = target.source_line - 1
    if idx < 0 or idx >= len(lines):
        _log("error", f"Could not locate task in file.")
        return CommandOutcome(refreshed, view_state)

    # Get blocker titles before removing
    blockers = extract_blockers_from_line(lines[idx])
    if not blockers:
        _log("info", f"Task {task_id} has no blockers.")
        return CommandOutcome(refreshed, view_state)

    # Remove all blockedby: from this task
    lines[idx] = remove_all_blocker_metadata(lines[idx])

    # Remove corresponding blocks: from each blocker task
    for blocker_title in blockers:
        blocker_task = find_task_by_title_match(refreshed, blocker_title)
        if blocker_task and blocker_task.source_line:
            b_idx = blocker_task.source_line - 1
            if 0 <= b_idx < len(lines):
                lines[b_idx] = remove_blocks_metadata(lines[b_idx], _strip_tags(target.title))

    write_journal(context.journal_path, "\n".join(lines))
    _save_undo_snapshot(context, snapshot)
    _log("info", f"Removed {len(blockers)} blocker(s) from task {task_id}.")
    clear_screen()
    refreshed = context.refresh_tasks()
    _render(refreshed, view_state)
    return CommandOutcome(refreshed, view_state)


def handle_block(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'block|blocker <blocked_id> <blocker_id>' command."""
    if not re.match(r"^\s*(?:block|blocker)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:block|blocker)\s+(\S+)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        from .tm_form import show_form, TextField
        # Pre-fill if partial ID was given
        partial = re.match(r"^\s*(?:block|blocker)\s+(\S+)\s*$", raw_command, re.IGNORECASE)
        form_fields = [
            TextField("Blocked ID", value=partial.group(1) if partial else "", placeholder="ID of task being blocked"),
            TextField("Blocker ID", placeholder="ID of blocking task"),
        ]
        result = show_form("Block Dependency", form_fields)
        if result is None:
            clear_screen()
            _render(refreshed, view_state)
            _log("info", f"Cancelled.")
            return CommandOutcome(refreshed, view_state)
        blocked_id = result["Blocked ID"].strip()
        blocker_id = result["Blocker ID"].strip()
        if not blocked_id or not blocker_id:
            clear_screen()
            _render(refreshed, view_state)
            _log("error", f"Both IDs are required.")
            return CommandOutcome(refreshed, view_state)
    else:
        blocked_id = match.group(1)
        blocker_id = match.group(2)

    blocked_task = find_task_by_id(refreshed, blocked_id)
    blocker_task = find_task_by_id(refreshed, blocker_id)

    if not blocked_task or isinstance(blocked_task, Subtask):
        _log("error", f"Task {blocked_id} not found.")
        return CommandOutcome(refreshed, view_state)
    if not blocker_task or isinstance(blocker_task, Subtask):
        _log("error", f"Task {blocker_id} not found.")
        return CommandOutcome(refreshed, view_state)

    snapshot = read_journal_snapshot(context.journal_path)
    lines = Path(context.journal_path).read_text(encoding="utf-8").split("\n")
    updated = False

    # Add blockedby: to the blocked task using source_line
    if blocked_task.source_line:
        idx = blocked_task.source_line - 1
        if 0 <= idx < len(lines):
            lines[idx] = add_blocker_metadata(lines[idx], _strip_tags(blocker_task.title))
            updated = True

    if updated and blocker_task.source_line:
        # Add blocks: to the blocker task using source_line
        idx = blocker_task.source_line - 1
        if 0 <= idx < len(lines):
            lines[idx] = add_blocks_metadata(lines[idx], _strip_tags(blocked_task.title))

    if updated:
        write_journal(context.journal_path, "\n".join(lines))
        _save_undo_snapshot(context, snapshot)
        _log("info", f"Task {blocked_id} is now blocked by task {blocker_id}.")
    else:
        _log("error", f"Could not update dependency.")

    updated_tasks = context.refresh_tasks()
    clear_screen()
    _render(updated_tasks, view_state)
    return CommandOutcome(updated_tasks, view_state)


def handle_pomodoro(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'pom|pomodoro' command."""
    if not re.match(r"^\s*(?:pom|pomodoro)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:pom|pomodoro)(?:\s+(\S+))?(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
    task_id = match.group(1) if match and match.group(1) else None
    minutes = int(match.group(2)) if match and match.group(2) else 25

    target = None
    task_title = ""
    if task_id:
        target = find_task_by_id(refreshed, task_id)
        if not target:
            _log("error", f"Task {task_id} not found.")
            return CommandOutcome(refreshed, view_state)
        task_title = target.title

    elapsed = run_pomodoro(minutes, task_title)

    # Log time to task if specified
    if target and task_id and not isinstance(target, Subtask):
        snapshot = read_journal_snapshot(context.journal_path)
        if _log_time_to_task(context, target, elapsed):
            _save_undo_snapshot(context, snapshot)
            _log("info", f"Logged {format_time_spent(elapsed)} to task {task_id}.")

    updated_tasks = context.refresh_tasks()
    return CommandOutcome(updated_tasks, view_state)


def handle_burndown(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'bd|burndown' command."""
    if not re.match(r"^\s*(?:bd|burndown)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:bd|burndown)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
    days = int(match.group(1)) if match and match.group(1) else 14
    chart = generate_burndown(refreshed, days)
    print(f"\n{Colors.HEADER}{chart}{Colors.RESET}")
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


def handle_kanban(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'kb|kanban' command."""
    if command not in ("kb", "kanban"):
        return None

    refreshed = context.refresh_tasks()
    print(f"\n{Colors.HEADER}{Colors.BOLD}Kanban Board{Colors.RESET}\n")
    print(render_kanban(refreshed))
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


def handle_project(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'pj|project' command."""
    if not re.match(r"^\s*(?:pj|project)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:pj|project)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
    tag_arg = match.group(1).strip() if match and match.group(1) else None

    if not tag_arg:
        # List all tags
        all_tags = get_all_tags(refreshed)
        if not all_tags:
            _log("info", f"No tags found in tasks.")
            return CommandOutcome(refreshed, view_state, skip_redraw=True)
        print(f"\n{Colors.HEADER}{Colors.BOLD}Project Tags{Colors.RESET}")
        print(f"{Colors.HEADER}{'─' * 40}{Colors.RESET}")
        for tag, count in sorted(all_tags.items(), key=lambda x: x[1], reverse=True):
            print(f"  #{tag:<20} {count} task(s)")
        print(f"{Colors.HEADER}{'─' * 40}{Colors.RESET}")
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    # Show tasks for specific tag
    tag = tag_arg.lstrip("#")
    tasks = get_tasks_by_tag(refreshed, tag)
    if not tasks:
        _log("info", f"No tasks found with tag #{tag}.")
        return CommandOutcome(refreshed, view_state, skip_redraw=True)

    tw = shutil.get_terminal_size((80, 24)).columns
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'─' * 3} #{tag} ({len(tasks)} tasks) {'─' * max(0, tw - len(tag) - 18)}{Colors.RESET}")
    for task in tasks:
        state_color = _get_state_color_inline(task.state)
        task_id = task.task_id or "?"
        priority_badge = f" [P:{task.priority}]" if task.priority else ""
        due = f" [DUE:{task.due_date.strftime('%d/%m/%Y')}]" if task.due_date else ""
        print(
            f"  [{task_id}] {state_color}{task.state:<{11}}{Colors.RESET} "
            f"{_title_without_tags_cmd(task.title)}{Colors.DIM}{priority_badge}{due}{Colors.RESET}"
        )
        for st in task.subtasks:
            st_color = _get_state_color_inline(st.state)
            st_due = f" [DUE:{st.due_date.strftime('%d/%m/%Y')}]" if st.due_date else ""
            print(f"       + [{st.task_id}] {st_color}{st.state:<{11}}{Colors.RESET} {_title_without_tags_cmd(st.title)}{Colors.DIM}{st_due}{Colors.RESET}")
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


def handle_export(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'export' command."""
    if not re.match(r"^\s*export\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*export\s+(\w+)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
    if not match:
        # Show form
        from .tm_form import show_form, TextField, SelectField
        form_fields = [
            SelectField("Format", ["json", "csv", "md"]),
            TextField("File path", placeholder="(optional, auto-generated)"),
        ]
        result = show_form("Export Tasks", form_fields)
        if result is None:
            clear_screen()
            _render(refreshed, view_state)
            _log("info", f"Cancelled.")
            return CommandOutcome(refreshed, view_state)
        fmt = result["Format"]
        filepath = result.get("File path", "").strip() or None
    else:
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
        _log("error", f"Unsupported format: {fmt}. Use json, csv, or md.")
        return CommandOutcome(refreshed, view_state)

    if not filepath:
        journal_dir = Path(context.journal_path).parent
        filepath = str(journal_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}")

    try:
        Path(filepath).write_text(content, encoding="utf-8")
        _log("info", f"Exported to: {filepath}")
    except OSError as exc:
        _log("error", f"Export failed: {exc}")
    return CommandOutcome(refreshed, view_state)


def handle_import(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'import' command."""
    if not re.match(r"^\s*import\b", raw_command, re.IGNORECASE):
        return None

    match = re.match(r"^\s*import\s+(.+)\s*$", raw_command, re.IGNORECASE)
    if not match:
        _log("error", f"Usage: import <filepath>")
        return CommandOutcome(tasks_by_date, view_state)

    import_path = match.group(1).strip()
    try:
        json_text = Path(import_path).read_text(encoding="utf-8")
    except OSError as exc:
        _log("error", f"Cannot read file: {exc}")
        return CommandOutcome(tasks_by_date, view_state)

    new_lines = import_from_json(json_text)
    if not new_lines:
        _log("error", f"Could not parse JSON or file is empty.")
        return CommandOutcome(tasks_by_date, view_state)

    snapshot = read_journal_snapshot(context.journal_path)
    try:
        from .tm_journal import file_lock
        with file_lock:
            existing = context.journal_path.read_text(encoding="utf-8")
            write_journal(context.journal_path, existing + "".join(new_lines))
        _save_undo_snapshot(context, snapshot)
        refreshed = context.refresh_tasks()
        clear_screen()
        task_count = sum(1 for line in new_lines if line.strip().startswith("-"))
        _log("info", f"Imported {task_count} task(s) from {import_path}.")
        _render(refreshed, view_state)
        return CommandOutcome(refreshed, view_state)
    except OSError as exc:
        _log("error", f"Import failed: {exc}")
        return CommandOutcome(tasks_by_date, view_state)


def handle_weekly_report(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'wr|weekly' command."""
    if not re.match(r"^\s*(?:wr|weekly)\b", raw_command, re.IGNORECASE):
        return None

    refreshed = context.refresh_tasks()
    match = re.match(r"^\s*(?:wr|weekly)(?:\s+(\d+))?\s*$", raw_command, re.IGNORECASE)
    days = int(match.group(1)) if match and match.group(1) else int(get_setting("weekly_report_days", 7))
    report = generate_weekly_report(refreshed, days)
    print(f"\n{report}")
    return CommandOutcome(refreshed, view_state, skip_redraw=True)


def handle_sort(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'sort' command."""
    if not re.match(r"^\s*sort\b", raw_command, re.IGNORECASE):
        return None

    match = re.match(r"^\s*sort\s+(\w+)(?:\s+(asc|desc))?\s*$", raw_command, re.IGNORECASE)
    if not match:
        # Show form
        from .tm_form import show_form, SelectField
        _sort_options = ["priority", "due_date", "state", "none"]
        _dir_options = ["asc", "desc"]
        cur_sort_idx = _sort_options.index(view_state.sort_by) if view_state.sort_by in _sort_options else 3
        cur_dir_idx = _dir_options.index(view_state.sort_direction) if view_state.sort_direction in _dir_options else 0
        form_fields = [
            SelectField("Sort by", _sort_options, selected=cur_sort_idx),
            SelectField("Direction", _dir_options, selected=cur_dir_idx),
        ]
        result = show_form("Sort Tasks", form_fields)
        if result is None:
            clear_screen()
            _render(tasks_by_date, view_state)
            _log("info", f"Cancelled.")
            return CommandOutcome(tasks_by_date, view_state)
        sort_by = result["Sort by"]
        direction = result["Direction"]
    else:
        sort_by = match.group(1).lower()
        if sort_by not in ("priority", "due_date", "state", "none"):
            _log("error", f"Invalid sort: {sort_by}. Use priority, due_date, state, or none.")
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
    _log("info", f"Sort: {sort_by} {direction}")
    return CommandOutcome(updated_tasks, next_view)


def handle_email(
    raw_command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'email' command (send pending tasks report)."""
    if not re.match(r"^\s*(?:se|send|email)\b", raw_command, re.IGNORECASE):
        return None

    from .tm_email import send_email_report, EmailResult
    from .tm_logic import build_pending_email_body, get_pending_tasks

    refreshed = context.refresh_tasks()
    pending = get_pending_tasks(refreshed)
    if not pending:
        _log("info", "No pending tasks to email.")
        return CommandOutcome(refreshed, view_state)

    body = build_pending_email_body(pending)
    result: EmailResult = send_email_report(context.email_config, body)
    if result.success:
        _log("info", f"Email sent to {context.email_config.to_address}.")
    else:
        _log("error", f"Email failed: {result.error}")
    return CommandOutcome(refreshed, view_state)
