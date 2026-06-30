"""System-level API handlers — config, sync, blockers, time tracking, journals, status, log."""

from pathlib import Path

from src.tm_logic import find_task_by_id
from src.tm_features import parse_time_spent, format_time_spent

from ..serializers import error_response, json_response, read_body
from ..state import _state_proxy as _state


# ─── Journals API ───────────────────────────────────────────────────────────

def api_get_journals(handler, params):
    """GET /api/journals — list available journals."""
    journal_dir = Path(_state.journal_path).parent
    current_name = Path(_state.journal_path).name

    journals = sorted(
        [p for p in journal_dir.glob("*.txt") if p.is_file()],
        key=lambda p: p.name.lower(),
    )

    items = []
    for j in journals:
        items.append({
            "name": j.name,
            "stem": j.stem,
            "current": j.name == current_name,
        })

    json_response(handler, {"journals": items, "current": current_name})


def api_switch_journal(handler, params):
    """POST /api/journals/switch — switch the active journal."""
    body = read_body(handler)
    name = body.get("name", "").strip()
    if not name:
        json_response(handler, {"ok": False, "error": "Missing journal name"}, status=400)
        return

    journal_dir = Path(_state.journal_path).parent

    if not name.lower().endswith(".txt"):
        name += ".txt"

    target = None
    for p in journal_dir.glob("*.txt"):
        if p.is_file() and p.name.lower() == name.lower():
            target = p
            break

    if target is None:
        json_response(handler, {"ok": False, "error": f"Journal '{name}' not found"}, status=404)
        return

    _state.journal_path = str(target)
    _state._tasks_by_date = None

    from src.tm_web.server import _PROJECT_ROOT
    try:
        cache_path = _PROJECT_ROOT / ".last_journal"
        cache_path.write_text(f"{target.name}\n", encoding="utf-8")
    except OSError:
        pass

    json_response(handler, {"ok": True, "current": target.name})


# ─── Status API ─────────────────────────────────────────────────────────────

def api_get_status(handler, params):
    """GET /api/status — returns sync and jira configuration status."""
    import subprocess
    from src.tm_settings import load_secrets

    from src.tm_web.server import _PROJECT_ROOT

    project_dir = _PROJECT_ROOT
    secrets = load_secrets(project_dir)

    jira_ok = bool(
        secrets.get("jira_url")
        and secrets.get("jira_email")
        and secrets.get("jira_api_token")
    )

    journals_dir = Path(_state.journal_path).parent
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(journals_dir), capture_output=True, text=True, timeout=3,
        )
        sync_ok = result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        sync_ok = False

    json_response(handler, {"sync": sync_ok, "jira": jira_ok})


# ─── Log API ────────────────────────────────────────────────────────────────

def api_get_log(handler, params):
    """GET /api/log — returns application log history."""
    import time as _time
    from src.tm_log import _history

    entries = []
    for ts, cat, msg in _history:
        entries.append({
            "time": _time.strftime("%H:%M:%S", _time.localtime(ts)),
            "category": cat,
            "message": msg,
        })
    json_response(handler, {"entries": entries})


# ─── Config API ─────────────────────────────────────────────────────────────

def api_get_config(handler, params):
    """GET /api/config — returns user settings and secrets (masked)."""
    from src.tm_settings import load_settings, load_secrets

    from src.tm_web.server import _PROJECT_ROOT

    project_dir = _PROJECT_ROOT
    settings = load_settings(project_dir, force_reload=True)
    secrets = load_secrets(project_dir)

    masked_secrets = {}
    for key in ("jira_url", "jira_email", "jira_api_token", "jira_account_id", "sync_token"):
        val = secrets.get(key, "")
        if key == "jira_api_token" or key == "sync_token":
            masked_secrets[key] = "••••••••" if val else ""
        else:
            masked_secrets[key] = val

    email_settings = dict(settings.get("email", {}))
    if email_settings.get("smtp_password"):
        email_settings["smtp_password"] = "••••••••"

    json_response(handler, {
        "settings": {
            "sync": settings.get("sync", {}),
            "email": email_settings,
            "agenda_days": settings.get("agenda_days", 7),
            "date_format": settings.get("date_format", "%d/%m/%Y"),
            "default_state": settings.get("default_state", "BACKLOG"),
            "default_priority": settings.get("default_priority"),
            "show_done_default": settings.get("show_done_default", False),
            "states": settings.get("states", []),
            "finished_states": settings.get("finished_states", []),
            "progress_states": settings.get("progress_states", []),
            "testing_states": settings.get("testing_states", []),
            "priorities": settings.get("priorities", []),
            "kanban_columns": settings.get("kanban_columns", []),
            "sort_by": settings.get("sort_by", "none"),
            "sort_direction": settings.get("sort_direction", "asc"),
            "weekly_report_days": settings.get("weekly_report_days", 7),
            "max_undo": settings.get("max_undo", 20),
            "prompt_format": settings.get("prompt_format", ""),
            "web_theme": settings.get("web_theme", "auto"),
        },
        "secrets": masked_secrets,
    })


def api_save_config(handler, params):
    """POST /api/config — save settings and/or secrets."""
    from src.tm_settings import load_settings, save_settings, load_secrets, save_secrets

    from src.tm_web.server import _PROJECT_ROOT

    body = read_body(handler)
    project_dir = _PROJECT_ROOT

    errors = []

    if "settings" in body:
        current = load_settings(project_dir, force_reload=True)
        updates = body["settings"]
        safe_keys = ("sync", "email", "agenda_days", "date_format",
                     "default_state", "default_priority", "show_done_default",
                     "states", "finished_states", "progress_states",
                     "testing_states", "priorities", "kanban_columns",
                     "sort_by", "sort_direction", "weekly_report_days",
                     "max_undo", "prompt_format")
        for key in safe_keys:
            if key in updates:
                if key == "email":
                    current_email = current.get("email", {})
                    new_email = updates["email"]
                    pwd = new_email.get("smtp_password", "")
                    if not pwd or pwd == "••••••••":
                        new_email["smtp_password"] = current_email.get("smtp_password", "")
                    current["email"] = new_email
                else:
                    current[key] = updates[key]
        if not save_settings(current, project_dir):
            errors.append("Failed to save settings")

    if "secrets" in body:
        current_secrets = load_secrets(project_dir)
        updates_secrets = body["secrets"]
        for key in ("jira_url", "jira_email", "jira_api_token", "jira_account_id", "sync_token"):
            if key in updates_secrets:
                val = updates_secrets[key]
                if val and val != "••••••••":
                    current_secrets[key] = val
                elif val == "":
                    current_secrets[key] = ""
        if not save_secrets(project_dir, current_secrets):
            errors.append("Failed to save secrets")

    if errors:
        json_response(handler, {"ok": False, "errors": errors}, status=500)
    else:
        json_response(handler, {"ok": True})


# ─── Sync API ───────────────────────────────────────────────────────────────

def api_get_sync_status(handler, params):
    """GET /api/sync/status — returns sync configuration and status."""
    try:
        from src.tm_sync import get_sync_info
        info = get_sync_info()
        json_response(handler, info)
    except Exception as e:
        json_response(handler, {
            "configured": False,
            "enabled": False,
            "remote": None,
            "branch": None,
            "status": "error",
            "pending_files": 0,
            "last_error": str(e),
            "history": []
        })


def _sync_conflict_strategy(params: dict):
    """Extract conflict strategy from query params or body."""
    strategy = params.get("strategy", [None])[0]
    if strategy not in ("keep_local", "use_remote", "merge", "skip", None):
        return None
    return strategy


def api_post_sync_pull(handler, params):
    """POST /api/sync/pull — pull remote changes with conflict strategy."""
    try:
        from src.tm_sync import sync_pull, is_configured

        if not is_configured():
            json_response(handler, {"success": False, "error": "Sync not configured"})
            return

        body = read_body(handler)
        strategy = body.get("strategy") or _sync_conflict_strategy(params)

        success = sync_pull(interactive=False, conflict_strategy=strategy)
        if success:
            json_response(handler, {"success": True})
        else:
            json_response(handler, {"success": False, "error": "Pull failed"})
    except Exception as e:
        json_response(handler, {"success": False, "error": str(e)})


def api_post_sync_push(handler, params):
    """POST /api/sync/push — sync (pull + push) with optional conflict strategy."""
    try:
        from src.tm_sync import sync_push_blocking, is_configured

        if not is_configured():
            json_response(handler, {"success": False, "error": "Sync not configured"})
            return

        body = read_body(handler)
        strategy = body.get("strategy") or _sync_conflict_strategy(params)

        success = sync_push_blocking(conflict_strategy=strategy)
        if success:
            json_response(handler, {"success": True})
        else:
            json_response(handler, {"success": False, "error": "Sync failed - check terminal for details"})
    except Exception as e:
        json_response(handler, {"success": False, "error": str(e)})


def api_post_sync_settings(handler, params):
    """POST /api/sync/settings — save remote and branch settings."""
    try:
        from src.tm_settings import load_settings, save_settings

        from src.tm_web.server import _PROJECT_ROOT

        project_dir = _PROJECT_ROOT
        settings = load_settings(project_dir, force_reload=True)

        if "sync" not in settings:
            settings["sync"] = {}

        remote = params.get("remote", "").strip()
        branch = params.get("branch", "").strip() or "main"

        settings["sync"]["remote"] = remote
        settings["sync"]["branch"] = branch

        save_settings(project_dir, settings)
        json_response(handler, {"success": True})
    except Exception as e:
        json_response(handler, {"success": False, "error": str(e)})


# ─── Time Tracking ──────────────────────────────────────────────────────────

def api_log_time(handler, params):
    """POST /api/tasks/<id>/time — log time to a task."""
    body = read_body(handler)
    task_id = params.get("task_id", [None])[0]
    time_str = body.get("time", "").strip()
    minutes_raw = body.get("minutes")

    if not task_id:
        error_response(handler, "task_id is required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        error_response(handler, f"Task {task_id} not found", 404)
        return

    from src.tm_models import Subtask
    if isinstance(task, Subtask):
        error_response(handler, "Time tracking only works on parent tasks")
        return

    from src.tm_features import parse_time_spent, format_time_spent

    if minutes_raw is not None:
        minutes = int(minutes_raw)
    elif time_str:
        minutes = parse_time_spent(time_str)
        if minutes is None:
            error_response(handler, f"Invalid time format: {time_str}. Use e.g. 2h, 30m, 1h30m")
            return
    else:
        error_response(handler, "time or minutes is required")
        return

    if minutes <= 0:
        error_response(handler, "Time must be positive")
        return

    try:
        from src.tm_journal import file_lock, write_journal
        from src.tm_features import extract_time_spent_from_line, update_time_in_line
        from src.tm_views_data import _task_to_view_item
        from ..serializers import serialize_task
        import re

        with file_lock:
            lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
            line_index = task.source_line - 1
            if line_index >= len(lines):
                error_response(handler, "Task line not found in journal", 500)
                return

            task_line = lines[line_index]
            existing = extract_time_spent_from_line(task_line)
            if existing is None:
                for j in range(line_index + 1, len(lines)):
                    cline = lines[j]
                    if not cline or not cline[0].isspace():
                        break
                    if re.search(r"--\s*(?:spent|time)\s*[:=]\s*(\S+)", cline, re.IGNORECASE):
                        existing = extract_time_spent_from_line(cline)
                        lines.pop(j)
                        break

            total = (existing or 0) + minutes
            lines[line_index] = update_time_in_line(task_line, total)
            write_journal(_state.journal_path, "\n".join(lines))

        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        json_response(handler, {
            "ok": True,
            "task": serialize_task(_task_to_view_item(updated)) if updated else None,
            "logged": format_time_spent(minutes),
            "total": format_time_spent(total),
        })
    except Exception as e:
        error_response(handler, str(e), 500)


# ─── Blocker Endpoints ─────────────────────────────────────────────────────

def api_add_blocker(handler, params):
    """POST /api/blockers/add — add a blocker relationship."""
    body = read_body(handler)
    blocked_id = body.get("blocked_id", "").strip()
    blocker_id = body.get("blocker_id", "").strip()

    if not blocked_id or not blocker_id:
        error_response(handler, "blocked_id and blocker_id are required")
        return

    blocked_task = find_task_by_id(_state.tasks_by_date, blocked_id)
    blocker_task = find_task_by_id(_state.tasks_by_date, blocker_id)

    if not blocked_task:
        error_response(handler, f"Task {blocked_id} not found", 404)
        return
    if not blocker_task:
        error_response(handler, f"Task {blocker_id} not found", 404)
        return

    from src.tm_models import Subtask
    if isinstance(blocked_task, Subtask) or isinstance(blocker_task, Subtask):
        error_response(handler, "Blockers only work on parent tasks")
        return

    try:
        from src.tm_features import add_blocker_metadata, add_blocks_metadata
        from src.tm_journal import file_lock, write_journal
        from src.tm_cmd_common import _strip_inline_tags

        blocked_title = _strip_inline_tags(blocked_task.title)
        blocker_title = _strip_inline_tags(blocker_task.title)

        with file_lock:
            lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
            updated = False

            if blocked_task.source_line:
                idx = blocked_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = add_blocker_metadata(lines[idx], blocker_title)
                    updated = True

            if updated and blocker_task.source_line:
                idx = blocker_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = add_blocks_metadata(lines[idx], blocked_title)

            if updated:
                write_journal(_state.journal_path, "\n".join(lines))

        if updated:
            _state.refresh()
            json_response(handler, {
                "ok": True,
                "message": f"Task {blocked_id} is now blocked by task {blocker_id}",
            })
        else:
            error_response(handler, "Could not update blocker relationship", 500)
    except Exception as e:
        error_response(handler, str(e), 500)


def api_delete_blocker(handler, params):
    """POST /api/blockers/delete — remove a blocker relationship."""
    body = read_body(handler)
    blocked_id = body.get("blocked_id", "").strip()
    blocker_id = body.get("blocker_id", "").strip()

    if not blocked_id or not blocker_id:
        error_response(handler, "blocked_id and blocker_id are required")
        return

    blocked_task = find_task_by_id(_state.tasks_by_date, blocked_id)
    blocker_task = find_task_by_id(_state.tasks_by_date, blocker_id)

    if not blocked_task:
        error_response(handler, f"Task {blocked_id} not found", 404)
        return
    if not blocker_task:
        error_response(handler, f"Task {blocker_id} not found", 404)
        return

    from src.tm_models import Subtask
    if isinstance(blocked_task, Subtask) or isinstance(blocker_task, Subtask):
        error_response(handler, "Blockers only work on parent tasks")
        return

    try:
        from src.tm_features import remove_blocker_metadata, remove_blocks_metadata
        from src.tm_journal import file_lock, write_journal
        from src.tm_cmd_common import _strip_inline_tags

        blocked_title = _strip_inline_tags(blocked_task.title)
        blocker_title = _strip_inline_tags(blocker_task.title)

        with file_lock:
            lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
            updated = False

            if blocked_task.source_line:
                idx = blocked_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = remove_blocker_metadata(lines[idx], blocker_title)
                    updated = True

            if updated and blocker_task.source_line:
                idx = blocker_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = remove_blocks_metadata(lines[idx], blocked_title)

            if updated:
                write_journal(_state.journal_path, "\n".join(lines))

        if updated:
            _state.refresh()
            json_response(handler, {
                "ok": True,
                "message": f"Removed blocker: {blocker_id} no longer blocks {blocked_id}",
            })
        else:
            error_response(handler, "Could not remove blocker relationship", 500)
    except Exception as e:
        error_response(handler, str(e), 500)
