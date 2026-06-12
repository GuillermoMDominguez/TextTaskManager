"""HTTP server and REST API for TextTaskManager web UI.

Zero external dependencies — pure stdlib.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

# Add project root to path for imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.tm_journal import (
    parse_journal,
    add_task_to_file,
    update_task_state_in_file,
    update_task_metadata_in_file,
    edit_task_title_in_file,
    delete_task_in_file,
    add_note_to_task_in_file,
    delete_note_in_file,
    edit_note_in_file,
    add_subtask_to_task,
    update_subtask_state_in_file,
    edit_subtask_title_in_file,
    delete_subtask_in_file,
    update_subtask_metadata_in_file,
    add_note_to_subtask_in_file,
)
from src.tm_logic import assign_task_ids, get_id_width, normalize_state_input, find_task_by_id
from src.tm_models import Task
from src.tm_config import VALID_STATES, VALID_PRIORITIES
from src.tm_views_data import (
    get_agenda_data,
    get_kanban_data,
    get_stats_data,
    get_all_tasks_flat,
    get_pending_tasks,
    get_weekly_report_data,
    get_burndown_data,
    get_blockers_data,
    get_time_tracking_data,
    get_all_tags_data,
    get_calendar_data,
    _task_to_view_item,
)

_STATIC_DIR = Path(__file__).parent / "static"


class WebState:
    """Shared state for the web server."""

    def __init__(self, journal_path: str):
        self.journal_path = journal_path
        self.lock = threading.Lock()
        self._tasks_by_date: dict = {}
        self.refresh()

    def refresh(self) -> dict:
        """Re-parse journal and assign IDs."""
        with self.lock:
            self._tasks_by_date = parse_journal(self.journal_path)
            assign_task_ids(self._tasks_by_date)
        return self._tasks_by_date

    @property
    def tasks_by_date(self) -> dict:
        return self._tasks_by_date


# Global state — set when server starts
_state: Optional[WebState] = None


def _read_lines_from_file(filepath: str) -> list:
    """Read all lines from a file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.readlines()


def _write_lines_to_file(filepath: str, lines: list) -> None:
    """Write all lines to a file."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _json_response(handler: "TTMRequestHandler", data: Any, status: int = 200) -> None:
    """Send a JSON response."""
    body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _error_response(handler: "TTMRequestHandler", message: str, status: int = 400) -> None:
    """Send an error JSON response."""
    _json_response(handler, {"error": message}, status)


def _read_body(handler: "TTMRequestHandler") -> dict:
    """Read and parse JSON body from request."""
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _serialize_task(item) -> dict:
    """Serialize a TaskViewItem to dict."""
    return {
        "id": item.task_id,
        "title": item.title,
        "state": item.state,
        "priority": item.priority,
        "due_date": item.due_date.strftime("%d/%m/%Y") if item.due_date else None,
        "tags": item.tags,
        "notes": item.notes,
        "subtasks": [
            {
                "id": st.task_id,
                "title": st.title,
                "state": st.state,
                "due_date": st.due_date.strftime("%d/%m/%Y") if st.due_date else None,
                "priority": st.priority,
                "tags": st.tags or [],
                "notes": st.notes or [],
            }
            for st in item.subtasks
        ],
        "time_spent": item.time_spent,
        "jira_key": item.jira_key,
    }


# ─── API Route Handlers ───────────────────────────────────────────────────────

def api_get_tasks(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/tasks — list all tasks."""
    _state.refresh()
    view = params.get("view", ["pending"])[0]

    if view == "all":
        items = get_all_tasks_flat(_state.tasks_by_date)
    else:
        items = get_pending_tasks(_state.tasks_by_date)

    _json_response(handler, {
        "tasks": [_serialize_task(item) for item in items],
        "id_width": get_id_width(_state.tasks_by_date),
        "states": VALID_STATES,
        "priorities": VALID_PRIORITIES,
    })


def api_get_agenda(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/agenda — agenda view data."""
    _state.refresh()
    days = int(params.get("days", ["7"])[0])
    data = get_agenda_data(_state.tasks_by_date, days)

    _json_response(handler, {
        "overdue": [_serialize_task(t) for t in data.overdue],
        "due_today": [_serialize_task(t) for t in data.due_today],
        "due_soon": [_serialize_task(t) for t in data.due_soon],
        "days_ahead": data.days_ahead,
    })


def api_get_calendar(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/calendar — calendar view data.
    
    Query params:
        view: 'week' or 'month' (default: 'month')
        year: target year (default: current)
        month: target month (default: current)
        day: target day for week view (default: today)
    """
    _state.refresh()
    
    view = params.get("view", ["month"])[0]
    year = int(params.get("year", [0])[0]) or None
    month = int(params.get("month", [0])[0]) or None
    day = int(params.get("day", [0])[0]) or None
    
    data = get_calendar_data(_state.tasks_by_date, view=view, year=year, month=month, day=day)
    
    # Serialize tasks in each day
    days_serialized = {}
    for date_str, tasks in data.days.items():
        days_serialized[date_str] = [_serialize_task(t) for t in tasks]
    
    _json_response(handler, {
        "view": data.view,
        "year": data.year,
        "month": data.month,
        "start_date": data.start_date,
        "end_date": data.end_date,
        "days": days_serialized,
    })


def api_get_kanban(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/kanban — kanban board data."""
    _state.refresh()
    data = get_kanban_data(_state.tasks_by_date)

    columns = {}
    for col in data.columns:
        columns[col] = [_serialize_task(t) for t in data.column_tasks[col]]

    _json_response(handler, {"columns": data.columns, "tasks": columns})


def api_get_stats(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/stats — statistics."""
    _state.refresh()
    data = get_stats_data(_state.tasks_by_date)

    _json_response(handler, {
        "total": data.total,
        "by_state": data.by_state,
        "by_priority": data.by_priority,
        "overdue": data.overdue_count,
        "due_today": data.due_today_count,
        "due_this_week": data.due_this_week_count,
    })


def api_get_weekly_report(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/weekly — weekly report data."""
    _state.refresh()
    days = int(params.get("days", ["7"])[0])
    data = get_weekly_report_data(_state.tasks_by_date, days)

    _json_response(handler, {
        "days": data.days,
        "period_start": data.period_start,
        "period_end": data.period_end,
        "completed": [_serialize_task(t) for t in data.completed],
        "in_progress": [_serialize_task(t) for t in data.in_progress],
        "upcoming": [_serialize_task(t) for t in data.upcoming],
        "total": data.total,
        "total_done": data.total_done,
        "total_pending": data.total_pending,
    })


def api_get_burndown(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/burndown — burndown chart data."""
    _state.refresh()
    sprint_days = int(params.get("days", ["14"])[0])
    data = get_burndown_data(_state.tasks_by_date, sprint_days)

    _json_response(handler, {
        "sprint_days": data.sprint_days,
        "total_tasks": data.total_tasks,
        "current_remaining": data.current_remaining,
        "ideal_per_day": data.ideal_per_day,
        "velocity": data.velocity,
        "points": [{"date": p.date, "remaining": p.remaining} for p in data.points],
    })


def api_get_tags(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/tags — all tags with counts."""
    _state.refresh()
    tags = get_all_tags_data(_state.tasks_by_date)
    _json_response(handler, {"tags": tags})


def api_get_tag_tasks(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/tags/<tag> — tasks for a specific tag."""
    _state.refresh()
    tag = params.get("tag", [None])[0]
    if not tag:
        _error_response(handler, "tag parameter required")
        return

    from src.tm_views_data import get_tag_view_data
    data = get_tag_view_data(_state.tasks_by_date, tag)
    _json_response(handler, {
        "tag": data.tag,
        "tasks": [_serialize_task(t) for t in data.tasks],
    })


def api_get_blockers(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/blockers — tasks with blocker relationships."""
    _state.refresh()
    data = get_blockers_data(_state.tasks_by_date)

    _json_response(handler, {
        "blockers": [
            {
                "task": _serialize_task(b.task),
                "blocked_by": b.blocked_by,
                "blocks": b.blocks,
                "is_blocked": b.is_blocked,
            }
            for b in data
        ],
    })


def api_get_time_tracking(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/time — time tracking data."""
    _state.refresh()
    data = get_time_tracking_data(_state.tasks_by_date)

    from src.tm_features import get_total_time_spent, format_time_spent
    total_minutes = get_total_time_spent(_state.tasks_by_date)

    _json_response(handler, {
        "tasks": [
            {
                "task": _serialize_task(item.task),
                "minutes": item.minutes_spent,
                "formatted": item.formatted,
            }
            for item in data
        ],
        "total_minutes": total_minutes,
        "total_formatted": format_time_spent(total_minutes) if total_minutes > 0 else "0m",
    })


def api_get_jira(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/jira — Jira integration data."""
    try:
        from src import tm_jira
        from src.tm_jira import is_configured, init_jira, _get_active_issues
    except ImportError:
        _json_response(handler, {"configured": False, "issues": [], "error": "Jira module not available"})
        return

    # Ensure Jira is initialized (needs project_dir for secrets)
    if not is_configured():
        # Try journal parent, then project root
        journal_dir = Path(_state.journal_path).resolve().parent
        if not init_jira(journal_dir):
            init_jira(journal_dir.parent)

    if not is_configured():
        _json_response(handler, {"configured": False, "issues": []})
        return

    try:
        filter_type = params.get("filter", ["active"])[0]
        search_query = params.get("q", [""])[0]
        base_url = tm_jira._jira_url or ""

        if filter_type == "active":
            result = _get_active_issues()
        elif filter_type in ("todo", "progress", "done", "review", "blocked", "cancelled"):
            from src.tm_jira import _get_filtered_issues
            result = _get_filtered_issues(filter_type)
        elif filter_type == "overdue":
            from src.tm_jira import _get_overdue
            result = _get_overdue()
        elif filter_type == "find" and search_query:
            from src.tm_jira import _search_issues
            result = _search_issues(search_query)
        elif filter_type == "notify":
            from src.tm_jira import _get_unread_comments
            messages = _get_unread_comments()
            _json_response(handler, {"configured": True, "notifications": messages, "base_url": base_url})
            return
        else:
            result = _get_active_issues()

        # _api_search returns {"issues": [...], ...} or None
        issues = []
        if result and isinstance(result, dict):
            issues = result.get("issues", [])
        elif result and isinstance(result, list):
            issues = result

        serialized = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            fields = issue.get("fields", {})
            serialized.append({
                "key": issue.get("key"),
                "summary": fields.get("summary"),
                "status": fields.get("status", {}).get("name") if isinstance(fields.get("status"), dict) else None,
                "priority": fields.get("priority", {}).get("name") if isinstance(fields.get("priority"), dict) else None,
                "project": fields.get("project", {}).get("key") if isinstance(fields.get("project"), dict) else None,
                "due_date": fields.get("duedate"),
                "type": fields.get("issuetype", {}).get("name") if isinstance(fields.get("issuetype"), dict) else None,
            })

        _json_response(handler, {"configured": True, "issues": serialized, "base_url": base_url})
    except Exception as e:
        _json_response(handler, {"configured": True, "issues": [], "error": str(e)})


def api_get_jira_transitions(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/jira/transitions?key=ISSUE-123 — get available transitions for a Jira issue."""
    try:
        from src import tm_jira
        from src.tm_jira import is_configured, init_jira, get_issue_transitions
    except ImportError:
        _json_response(handler, {"error": "Jira module not available"}, 500)
        return

    if not is_configured():
        journal_dir = Path(_state.journal_path).resolve().parent
        if not init_jira(journal_dir):
            init_jira(journal_dir.parent)

    if not is_configured():
        _json_response(handler, {"error": "Jira not configured"}, 400)
        return

    issue_key = params.get("key", [""])[0].strip()
    if not issue_key:
        _error_response(handler, "key parameter is required")
        return

    transitions = get_issue_transitions(issue_key)
    _json_response(handler, {"key": issue_key, "transitions": transitions})


def api_post_jira_transition(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/jira/transition — transition a Jira issue to a new status."""
    try:
        from src import tm_jira
        from src.tm_jira import is_configured, init_jira, transition_issue
    except ImportError:
        _json_response(handler, {"error": "Jira module not available"}, 500)
        return

    if not is_configured():
        journal_dir = Path(_state.journal_path).resolve().parent
        if not init_jira(journal_dir):
            init_jira(journal_dir.parent)

    if not is_configured():
        _json_response(handler, {"error": "Jira not configured"}, 400)
        return

    body = _read_body(handler)
    issue_key = body.get("key", "").strip()
    transition_id = body.get("transition_id", "").strip()

    if not issue_key or not transition_id:
        _error_response(handler, "key and transition_id are required")
        return

    success = transition_issue(issue_key, transition_id)
    if success:
        _json_response(handler, {"ok": True, "key": issue_key})
    else:
        _error_response(handler, f"Failed to transition {issue_key}", 500)


# ─── Write API Handlers ───────────────────────────────────────────────────────

def api_change_state(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/state — change task state."""
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0]
    new_state = body.get("state", "")

    if not task_id or not new_state:
        _error_response(handler, "task_id and state are required")
        return

    normalized = normalize_state_input(new_state)
    if not normalized:
        _error_response(handler, f"Invalid state: {new_state}")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        update_task_state_in_file(_state.journal_path, task, normalized)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            _json_response(handler, _serialize_task(_task_to_view_item(updated)))
        else:
            _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_create_task(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks — create a new task."""
    body = _read_body(handler)
    title = body.get("title", "").strip()
    state = body.get("state", "BACKLOG")
    due_date = body.get("due_date")
    priority = body.get("priority")
    jira_key = body.get("jira_key")

    if not title:
        _error_response(handler, "title is required")
        return

    try:
        from datetime import datetime
        date_obj = None
        if due_date:
            try:
                date_obj = datetime.strptime(due_date, "%d/%m/%Y")
            except ValueError:
                try:
                    date_obj = datetime.strptime(due_date, "%d/%m/%y")
                except ValueError:
                    # Try ISO format as fallback
                    try:
                        date_obj = datetime.strptime(due_date, "%Y-%m-%d")
                    except ValueError:
                        pass

        add_task_to_file(
            _state.journal_path,
            title=title,
            state=state,
            due_date=date_obj,
            priority=priority,
            jira_key=jira_key.strip().upper() if jira_key else None,
        )
        _state.refresh()
        _json_response(handler, {"ok": True}, 201)
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_edit_task(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/edit — edit task title, priority, due date."""
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0] or body.get("task_id")

    if not task_id:
        _error_response(handler, "task_id is required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        from datetime import datetime

        # Edit title if provided (tags are applied to title)
        new_title = body.get("title", "").strip()
        new_tags = body.get("tags") if "tags" in body else None

        # Apply tags to title
        if new_tags is not None:
            from src.tm_cmd_common import _apply_tags_to_text
            clean_tags = [t.lstrip('#') for t in new_tags if t.strip()]
            base_title = new_title if new_title else task.title
            # Strip existing tags from base if we have explicit tags
            from src.tm_cmd_common import _strip_inline_tags
            base_title = _strip_inline_tags(base_title)
            new_title = _apply_tags_to_text(base_title, clean_tags)

        if new_title and new_title != task.title:
            edit_task_title_in_file(_state.journal_path, task, new_title)
            _state.refresh()
            task = find_task_by_id(_state.tasks_by_date, task_id)
            if not task:
                _json_response(handler, {"ok": True})
                return

        # Edit state if provided
        new_state = body.get("state", "").strip()
        if new_state and new_state != task.state:
            normalized = normalize_state_input(new_state)
            if normalized:
                update_task_state_in_file(_state.journal_path, task, normalized)
                _state.refresh()
                task = find_task_by_id(_state.tasks_by_date, task_id)
                if not task:
                    _json_response(handler, {"ok": True})
                    return

        # Edit metadata (priority, due_date, jira_key)
        new_priority = body.get("priority")
        new_due = body.get("due_date")
        new_jira_key = body.get("jira_key")

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

        # jira_key: None=keep, ""=remove, "KEY-123"=set
        jira_key_val = None  # means keep existing
        if new_jira_key is not None:
            jira_key_val = new_jira_key.strip().upper() if new_jira_key.strip() else ""

        if due_obj != task.due_date or priority_val != task.priority or jira_key_val is not None:
            update_task_metadata_in_file(
                _state.journal_path, task,
                due_date=due_obj,
                priority=priority_val,
                jira_key=jira_key_val,
            )

        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            _json_response(handler, _serialize_task(_task_to_view_item(updated)))
        else:
            _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_delete_task(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/delete — delete a task."""
    task_id = params.get("task_id", [None])[0]

    if not task_id:
        _error_response(handler, "task_id is required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        delete_task_in_file(_state.journal_path, task)
        _state.refresh()
        _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_add_note(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/notes — add a note to a task."""
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0]
    note = body.get("note", "").strip()

    if not task_id or not note:
        _error_response(handler, "task_id and note are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        add_note_to_task_in_file(_state.journal_path, task, note)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            _json_response(handler, _serialize_task(_task_to_view_item(updated)))
        else:
            _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_delete_note(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/notes/delete — delete a note from a task."""
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0]
    note_index = body.get("index")

    if not task_id or note_index is None:
        _error_response(handler, "task_id and index are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        delete_note_in_file(_state.journal_path, task, int(note_index))
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            _json_response(handler, _serialize_task(_task_to_view_item(updated)))
        else:
            _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_edit_note(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/notes/edit — edit a note on a task."""
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0]
    note_index = body.get("index")
    new_note = body.get("note", "").strip()

    if not task_id or note_index is None or not new_note:
        _error_response(handler, "task_id, index, and note are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    try:
        edit_note_in_file(_state.journal_path, task, int(note_index), new_note)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            _json_response(handler, _serialize_task(_task_to_view_item(updated)))
        else:
            _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_add_subtask(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/subtasks — add a subtask to a task."""
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0]
    title = body.get("title", "").strip()
    state = body.get("state", "BACKLOG")

    if not task_id or not title:
        _error_response(handler, "task_id and title are required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    # Only parent tasks can have subtasks
    if not hasattr(task, 'subtasks'):
        _error_response(handler, "Cannot add subtask to a subtask")
        return

    try:
        add_subtask_to_task(_state.journal_path, task, title, state)
        _state.refresh()
        updated = find_task_by_id(_state.tasks_by_date, task_id)
        if updated:
            _json_response(handler, _serialize_task(_task_to_view_item(updated)))
        else:
            _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_edit_subtask(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/subtasks/<id>/edit — edit a subtask (title, state, due_date, priority, notes)."""
    body = _read_body(handler)
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        _error_response(handler, "subtask_id is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        _error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        new_title = body.get("title", "").strip()
        new_state = body.get("state", "").strip()
        due_date_str = body.get("due_date", "").strip() if body.get("due_date") is not None else None
        priority_str = body.get("priority", "").strip() if body.get("priority") is not None else None
        note_to_add = body.get("add_note", "").strip() if body.get("add_note") else ""
        new_tags = body.get("tags") if "tags" in body else None

        # Apply tags to title if tags were provided
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

        # Update metadata (due_date, priority)
        if subtask and (due_date_str is not None or priority_str is not None):
            from datetime import datetime as dt
            new_due = None
            clear_due = False
            if due_date_str is not None:
                if due_date_str == "":
                    clear_due = True
                else:
                    try:
                        new_due = dt.strptime(due_date_str, "%Y-%m-%d")
                    except ValueError:
                        try:
                            new_due = dt.strptime(due_date_str, "%d/%m/%Y")
                        except ValueError:
                            try:
                                new_due = dt.strptime(due_date_str, "%d/%m/%y")
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

        # Add a note
        if subtask and note_to_add:
            add_note_to_subtask_in_file(_state.journal_path, subtask, note_to_add)
            _state.refresh()

        _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_delete_subtask(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/subtasks/<id>/delete — delete a subtask."""
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        _error_response(handler, "subtask_id is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        _error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        delete_subtask_in_file(_state.journal_path, subtask)
        _state.refresh()
        _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_add_subtask_note(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/subtasks/<id>/notes — add a note to a subtask."""
    body = _read_body(handler)
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        _error_response(handler, "subtask_id is required")
        return

    note = body.get("note", "").strip()
    if not note:
        _error_response(handler, "note is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        _error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        add_note_to_subtask_in_file(_state.journal_path, subtask, note)
        _state.refresh()
        _json_response(handler, {"ok": True})
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_delete_subtask_note(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/subtasks/<id>/notes/delete — delete a subtask note by index."""
    body = _read_body(handler)
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        _error_response(handler, "subtask_id is required")
        return

    note_index = body.get("note_index")
    if note_index is None:
        _error_response(handler, "note_index is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        _error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        idx = int(note_index)
        if idx < 0 or idx >= len(subtask.comments):
            _error_response(handler, f"Invalid note_index {idx}", 400)
            return
        # Delete by re-parsing: remove the note from file
        # Subtask notes are lines after the subtask line starting with ":"
        lines = _read_lines_from_file(_state.journal_path)
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
            _write_lines_to_file(_state.journal_path, lines)
            _state.refresh()
            _json_response(handler, {"ok": True})
        else:
            _error_response(handler, "Note line not found in file", 404)
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_edit_subtask_note(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/subtasks/<id>/notes/edit — edit a subtask note by index."""
    body = _read_body(handler)
    subtask_id = params.get("subtask_id", [None])[0]

    if not subtask_id:
        _error_response(handler, "subtask_id is required")
        return

    note_index = body.get("note_index")
    new_note = body.get("note", "").strip()
    if note_index is None:
        _error_response(handler, "note_index is required")
        return
    if not new_note:
        _error_response(handler, "note is required")
        return

    subtask = find_task_by_id(_state.tasks_by_date, subtask_id)
    if not subtask:
        _error_response(handler, f"Subtask {subtask_id} not found", 404)
        return

    try:
        idx = int(note_index)
        if idx < 0 or idx >= len(subtask.comments):
            _error_response(handler, f"Invalid note_index {idx}", 400)
            return
        lines = _read_lines_from_file(_state.journal_path)
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
            _write_lines_to_file(_state.journal_path, lines)
            _state.refresh()
            _json_response(handler, {"ok": True})
        else:
            _error_response(handler, "Note line not found in file", 404)
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_search_tasks(handler: "TTMRequestHandler", params: dict) -> None:
    """GET /api/search?q=... — search tasks by title/tag."""
    _state.refresh()
    query = params.get("q", [""])[0].strip().lower()

    if not query:
        _json_response(handler, {"tasks": []})
        return

    items = get_all_tasks_flat(_state.tasks_by_date)
    results = []
    for item in items:
        # Match title, tags, or notes
        if (query in item.title.lower()
                or any(query in tag.lower() for tag in item.tags)
                or any(query in n.lower() for n in item.notes)):
            results.append(_serialize_task(item))

    _json_response(handler, {"tasks": results, "query": query})


# ─── Journals API ─────────────────────────────────────────────────────────────

def api_get_journals(handler, params):
    """GET /api/journals — list available journals and identify the current one."""
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

    _json_response(handler, {"journals": items, "current": current_name})


def api_switch_journal(handler, params):
    """POST /api/journals/switch — switch the active journal."""
    body = _read_body(handler)
    name = body.get("name", "").strip()
    if not name:
        _json_response(handler, {"ok": False, "error": "Missing journal name"}, status=400)
        return

    journal_dir = Path(_state.journal_path).parent

    # Normalize extension
    if not name.lower().endswith(".txt"):
        name += ".txt"

    # Find matching journal (case-insensitive)
    target = None
    for p in journal_dir.glob("*.txt"):
        if p.is_file() and p.name.lower() == name.lower():
            target = p
            break

    if target is None:
        _json_response(handler, {"ok": False, "error": f"Journal '{name}' not found"}, status=404)
        return

    # Switch
    _state.journal_path = str(target)
    _state._tasks_by_date = None  # Force re-parse

    # Update .last_journal cache
    try:
        cache_path = _PROJECT_ROOT / ".last_journal"
        cache_path.write_text(f"{target.name}\n", encoding="utf-8")
    except OSError:
        pass

    _json_response(handler, {"ok": True, "current": target.name})


def api_get_status(handler, params):
    """GET /api/status — returns sync and jira configuration status."""
    import subprocess
    from src.tm_settings import load_secrets

    project_dir = _PROJECT_ROOT
    secrets = load_secrets(project_dir)

    # Jira is configured if URL + email + token are set
    jira_ok = bool(
        secrets.get("jira_url")
        and secrets.get("jira_email")
        and secrets.get("jira_api_token")
    )

    # Sync is configured if journals dir is a git repo with a remote
    journals_dir = Path(_state.journal_path).parent
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(journals_dir), capture_output=True, text=True, timeout=3,
        )
        sync_ok = result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        sync_ok = False

    _json_response(handler, {"sync": sync_ok, "jira": jira_ok})


# ─── Config API ───────────────────────────────────────────────────────────────

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
    _json_response(handler, {"entries": entries})


def api_get_config(handler, params):
    """GET /api/config — returns user settings and secrets (masked)."""
    from src.tm_settings import load_settings, load_secrets

    project_dir = _PROJECT_ROOT
    settings = load_settings(project_dir, force_reload=True)
    secrets = load_secrets(project_dir)

    # Mask secret values — only show if set or empty
    masked_secrets = {}
    for key in ("jira_url", "jira_email", "jira_api_token", "jira_account_id", "sync_token"):
        val = secrets.get(key, "")
        if key == "jira_api_token" or key == "sync_token":
            masked_secrets[key] = "••••••••" if val else ""
        else:
            masked_secrets[key] = val

    # Mask email password
    email_settings = dict(settings.get("email", {}))
    if email_settings.get("smtp_password"):
        email_settings["smtp_password"] = "••••••••"

    _json_response(handler, {
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

    body = _read_body(handler)
    project_dir = _PROJECT_ROOT

    errors = []

    # Save settings fields
    if "settings" in body:
        current = load_settings(project_dir, force_reload=True)
        updates = body["settings"]
        # Only allow updating specific safe keys
        safe_keys = ("sync", "email", "agenda_days", "date_format",
                     "default_state", "default_priority", "show_done_default",
                     "states", "finished_states", "progress_states",
                     "testing_states", "priorities", "kanban_columns",
                     "sort_by", "sort_direction", "weekly_report_days",
                     "max_undo", "prompt_format")
        for key in safe_keys:
            if key in updates:
                if key == "email":
                    # Merge email: don't overwrite smtp_password with mask/empty
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

    # Save secrets fields
    if "secrets" in body:
        current_secrets = load_secrets(project_dir)
        updates_secrets = body["secrets"]
        for key in ("jira_url", "jira_email", "jira_api_token", "jira_account_id", "sync_token"):
            if key in updates_secrets:
                val = updates_secrets[key]
                # Don't overwrite with mask placeholder
                if val and val != "••••••••":
                    current_secrets[key] = val
                elif val == "":
                    current_secrets[key] = ""
        if not save_secrets(project_dir, current_secrets):
            errors.append("Failed to save secrets")

    if errors:
        _json_response(handler, {"ok": False, "errors": errors}, status=500)
    else:
        _json_response(handler, {"ok": True})


# ─── Time Tracking Endpoints ─────────────────────────────────────────────────

def api_log_time(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/tasks/<id>/time — log time to a task.

    Body: {"time": "2h30m"} or {"minutes": 90}
    """
    body = _read_body(handler)
    task_id = params.get("task_id", [None])[0]
    time_str = body.get("time", "").strip()
    minutes_raw = body.get("minutes")

    if not task_id:
        _error_response(handler, "task_id is required")
        return

    task = find_task_by_id(_state.tasks_by_date, task_id)
    if not task:
        _error_response(handler, f"Task {task_id} not found", 404)
        return

    from src.tm_models import Subtask
    if isinstance(task, Subtask):
        _error_response(handler, "Time tracking only works on parent tasks")
        return

    from src.tm_features import parse_time_spent, format_time_spent

    if minutes_raw is not None:
        minutes = int(minutes_raw)
    elif time_str:
        minutes = parse_time_spent(time_str)
        if minutes is None:
            _error_response(handler, f"Invalid time format: {time_str}. Use e.g. 2h, 30m, 1h30m")
            return
    else:
        _error_response(handler, "time or minutes is required")
        return

    if minutes <= 0:
        _error_response(handler, "Time must be positive")
        return

    try:
        from src.tm_journal import file_lock, write_journal
        from src.tm_features import extract_time_spent_from_line, update_time_in_line
        import re

        with file_lock:
            lines = Path(_state.journal_path).read_text(encoding="utf-8").split("\n")
            line_index = task.source_line - 1
            if line_index >= len(lines):
                _error_response(handler, "Task line not found in journal", 500)
                return

            task_line = lines[line_index]
            existing = extract_time_spent_from_line(task_line)
            # Check continuation lines too
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
        _json_response(handler, {
            "ok": True,
            "task": _serialize_task(_task_to_view_item(updated)) if updated else None,
            "logged": format_time_spent(minutes),
            "total": format_time_spent(total),
        })
    except Exception as e:
        _error_response(handler, str(e), 500)


# ─── Blocker Endpoints ────────────────────────────────────────────────────────

def api_add_blocker(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/blockers/add — add a blocker relationship.

    Body: {"blocked_id": "5", "blocker_id": "3"}
    Task <blocked_id> becomes blocked by task <blocker_id>.
    """
    body = _read_body(handler)
    blocked_id = body.get("blocked_id", "").strip()
    blocker_id = body.get("blocker_id", "").strip()

    if not blocked_id or not blocker_id:
        _error_response(handler, "blocked_id and blocker_id are required")
        return

    blocked_task = find_task_by_id(_state.tasks_by_date, blocked_id)
    blocker_task = find_task_by_id(_state.tasks_by_date, blocker_id)

    if not blocked_task:
        _error_response(handler, f"Task {blocked_id} not found", 404)
        return
    if not blocker_task:
        _error_response(handler, f"Task {blocker_id} not found", 404)
        return

    from src.tm_models import Subtask
    if isinstance(blocked_task, Subtask) or isinstance(blocker_task, Subtask):
        _error_response(handler, "Blockers only work on parent tasks")
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

            # Add blockedby: to the blocked task
            if blocked_task.source_line:
                idx = blocked_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = add_blocker_metadata(lines[idx], blocker_title)
                    updated = True

            # Add blocks: to the blocker task
            if updated and blocker_task.source_line:
                idx = blocker_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = add_blocks_metadata(lines[idx], blocked_title)

            if updated:
                write_journal(_state.journal_path, "\n".join(lines))

        if updated:
            _state.refresh()
            _json_response(handler, {
                "ok": True,
                "message": f"Task {blocked_id} is now blocked by task {blocker_id}",
            })
        else:
            _error_response(handler, "Could not update blocker relationship", 500)
    except Exception as e:
        _error_response(handler, str(e), 500)


def api_delete_blocker(handler: "TTMRequestHandler", params: dict) -> None:
    """POST /api/blockers/delete — remove a blocker relationship.

    Body: {"blocked_id": "5", "blocker_id": "3"}
    """
    body = _read_body(handler)
    blocked_id = body.get("blocked_id", "").strip()
    blocker_id = body.get("blocker_id", "").strip()

    if not blocked_id or not blocker_id:
        _error_response(handler, "blocked_id and blocker_id are required")
        return

    blocked_task = find_task_by_id(_state.tasks_by_date, blocked_id)
    blocker_task = find_task_by_id(_state.tasks_by_date, blocker_id)

    if not blocked_task:
        _error_response(handler, f"Task {blocked_id} not found", 404)
        return
    if not blocker_task:
        _error_response(handler, f"Task {blocker_id} not found", 404)
        return

    from src.tm_models import Subtask
    if isinstance(blocked_task, Subtask) or isinstance(blocker_task, Subtask):
        _error_response(handler, "Blockers only work on parent tasks")
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

            # Remove blockedby: from the blocked task
            if blocked_task.source_line:
                idx = blocked_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = remove_blocker_metadata(lines[idx], blocker_title)
                    updated = True

            # Remove blocks: from the blocker task
            if updated and blocker_task.source_line:
                idx = blocker_task.source_line - 1
                if 0 <= idx < len(lines):
                    lines[idx] = remove_blocks_metadata(lines[idx], blocked_title)

            if updated:
                write_journal(_state.journal_path, "\n".join(lines))

        if updated:
            _state.refresh()
            _json_response(handler, {
                "ok": True,
                "message": f"Removed blocker: {blocker_id} no longer blocks {blocked_id}",
            })
        else:
            _error_response(handler, "Could not remove blocker relationship", 500)
    except Exception as e:
        _error_response(handler, str(e), 500)


# ─── Route Table ──────────────────────────────────────────────────────────────

API_ROUTES = {
    # Read endpoints
    ("GET", "/api/tasks"): api_get_tasks,
    ("GET", "/api/agenda"): api_get_agenda,
    ("GET", "/api/calendar"): api_get_calendar,
    ("GET", "/api/kanban"): api_get_kanban,
    ("GET", "/api/stats"): api_get_stats,
    ("GET", "/api/weekly"): api_get_weekly_report,
    ("GET", "/api/burndown"): api_get_burndown,
    ("GET", "/api/tags"): api_get_tags,
    ("GET", "/api/tags/tasks"): api_get_tag_tasks,
    ("GET", "/api/blockers"): api_get_blockers,
    ("GET", "/api/time"): api_get_time_tracking,
    ("GET", "/api/jira"): api_get_jira,
    ("GET", "/api/jira/transitions"): api_get_jira_transitions,
    ("GET", "/api/search"): api_search_tasks,
    ("GET", "/api/config"): api_get_config,
    ("GET", "/api/log"): api_get_log,
    ("GET", "/api/status"): api_get_status,
    ("GET", "/api/journals"): api_get_journals,
    # Write endpoints
    ("POST", "/api/tasks"): api_create_task,
    ("POST", "/api/tasks/state"): api_change_state,
    ("POST", "/api/tasks/edit"): api_edit_task,
    ("POST", "/api/tasks/delete"): api_delete_task,
    ("POST", "/api/tasks/notes"): api_add_note,
    ("POST", "/api/tasks/notes/delete"): api_delete_note,
    ("POST", "/api/tasks/notes/edit"): api_edit_note,
    ("POST", "/api/tasks/subtasks"): api_add_subtask,
    ("POST", "/api/subtasks/edit"): api_edit_subtask,
    ("POST", "/api/subtasks/delete"): api_delete_subtask,
    ("POST", "/api/subtasks/notes"): api_add_subtask_note,
    ("POST", "/api/subtasks/notes/delete"): api_delete_subtask_note,
    ("POST", "/api/subtasks/notes/edit"): api_edit_subtask_note,
    ("POST", "/api/jira/transition"): api_post_jira_transition,
    ("POST", "/api/config"): api_save_config,
    ("POST", "/api/journals/switch"): api_switch_journal,
    ("POST", "/api/tasks/time"): api_log_time,
    ("POST", "/api/blockers/add"): api_add_blocker,
    ("POST", "/api/blockers/delete"): api_delete_blocker,
}


# ─── HTTP Handler ─────────────────────────────────────────────────────────────

class TTMRequestHandler(SimpleHTTPRequestHandler):
    """Custom handler: serves static files + JSON API."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_STATIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # API routes
        route_key = ("GET", parsed.path)
        if route_key in API_ROUTES:
            API_ROUTES[route_key](self, params)
            return

        # Static files — serve index.html for root
        if parsed.path == "/" or parsed.path == "":
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Extract task_id from paths like /api/tasks/3/state, /api/tasks/3/edit, etc.
        if parsed.path.startswith("/api/tasks/"):
            parts = parsed.path.split("/")
            # /api/tasks/<id>/<action>
            if len(parts) == 5:
                task_id = parts[3]
                action = parts[4]
                params["task_id"] = [task_id]

                route_key = ("POST", f"/api/tasks/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

            # /api/tasks/<id>/<sub>/<action> e.g. /api/tasks/3/notes/delete
            if len(parts) == 6:
                task_id = parts[3]
                sub = parts[4]
                action = parts[5]
                params["task_id"] = [task_id]

                route_key = ("POST", f"/api/tasks/{sub}/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

        # Extract subtask_id from paths like /api/subtasks/1.2/edit
        if parsed.path.startswith("/api/subtasks/"):
            parts = parsed.path.split("/")
            # /api/subtasks/<id>/<action> (5 parts)
            if len(parts) == 5:
                subtask_id = parts[3]
                action = parts[4]
                params["subtask_id"] = [subtask_id]

                route_key = ("POST", f"/api/subtasks/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return
            # /api/subtasks/<id>/<action>/<sub_action> (6 parts, e.g. notes/delete)
            elif len(parts) == 6:
                subtask_id = parts[3]
                action = f"{parts[4]}/{parts[5]}"
                params["subtask_id"] = [subtask_id]

                route_key = ("POST", f"/api/subtasks/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

        route_key = ("POST", parsed.path)
        if route_key in API_ROUTES:
            API_ROUTES[route_key](self, params)
            return

        _error_response(self, "Not found", 404)

    def do_DELETE(self):
        """Handle DELETE requests (mapped to POST handlers)."""
        self.do_POST()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging to keep terminal clean."""
        pass


# ─── Server Entry Point ───────────────────────────────────────────────────────

_server_instance: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_server_port: int = 8080


def get_url() -> str:
    """Return the URL of the running (or configured) server."""
    return f"http://127.0.0.1:{_server_port}"


def _is_port_in_use(port: int) -> bool:
    """Check if a port is already in use by another process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def is_running() -> bool:
    """Check if the background web server is running."""
    return _server_thread is not None and _server_thread.is_alive()


def _open_browser_app_mode(url: str) -> bool:
    """Open URL in browser's app mode (no address bar, looks like native app).
    
    Tries Chrome/Chromium/Edge/Brave in app mode, falls back to regular browser.
    Returns True if app mode was used, False if fell back to regular browser.
    """
    # Browser paths by platform
    if sys.platform == "darwin":  # macOS
        browsers = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif sys.platform == "win32":  # Windows
        browsers = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
    else:  # Linux
        browsers = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("brave-browser"),
            shutil.which("brave"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            shutil.which("microsoft-edge"),
        ]
        browsers = [b for b in browsers if b]  # Filter None values
    
    # Try each browser in app mode
    for browser_path in browsers:
        if browser_path and os.path.exists(browser_path):
            try:
                subprocess.Popen(
                    [browser_path, f"--app={url}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    
    # Fallback to regular browser
    webbrowser.open(url)
    return False


def start_server_background(journal_path: str, port: int = 8080, open_browser: bool = True) -> bool:
    """Start the web UI server in a background daemon thread.

    The server runs until stop_server() is called or the process exits.
    
    Returns:
        True if server started successfully, False if port already in use.
    """
    global _state, _server_instance, _server_thread, _server_port

    if is_running():
        return True

    # Check if port is already in use by another process
    if _is_port_in_use(port):
        # Port in use - just open the browser to existing server
        _server_port = port
        if open_browser:
            _open_browser_app_mode(get_url())
        return False

    _server_port = port
    _state = WebState(journal_path)
    _server_instance = HTTPServer(("127.0.0.1", port), TTMRequestHandler)
    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    _server_thread.start()

    if open_browser:
        _open_browser_app_mode(get_url())
    
    return True


def stop_server() -> None:
    """Stop the background web server."""
    global _server_instance, _server_thread

    if _server_instance is not None:
        _server_instance.shutdown()
        _server_instance.server_close()
        _server_instance = None
    _server_thread = None


def start_server(journal_path: str, port: int = 8080, open_browser: bool = True) -> None:
    """Start the web UI server (blocking, for standalone mode).

    Args:
        journal_path: Path to the journal .txt file.
        port: HTTP port (default 8080).
        open_browser: Whether to open the browser automatically.
    """
    global _state, _server_port
    _server_port = port
    _state = WebState(journal_path)

    server = HTTPServer(("127.0.0.1", port), TTMRequestHandler)
    url = get_url()

    print(f"\n  Web UI running at: \033[1m\033[96m{url}\033[0m")
    print(f"  Journal: {journal_path}")
    print(f"  Press Ctrl+C to stop\n")

    if open_browser:
        _open_browser_app_mode(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Web UI stopped.")
    finally:
        server.server_close()


# ─── Standalone mode ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TextTaskManager Web UI")
    parser.add_argument("--journal", "-j", required=True, help="Path to journal file")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Server port")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")

    args = parser.parse_args()
    start_server(args.journal, args.port, not args.no_browser)
