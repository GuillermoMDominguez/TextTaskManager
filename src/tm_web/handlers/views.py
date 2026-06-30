"""Data retrieval API handlers — views, stats, search, etc."""

from src.tm_config import VALID_PRIORITIES, VALID_STATES
from src.tm_logic import get_id_width
from src.tm_views_data import (
    get_agenda_data,
    get_all_tags_data,
    get_all_tasks_flat,
    get_blockers_data,
    get_burndown_data,
    get_calendar_data,
    get_gantt_data,
    get_kanban_data,
    get_pending_tasks,
    get_stats_data,
    get_tag_view_data,
    get_time_tracking_data,
    get_weekly_report_data,
)

from ..serializers import error_response, json_response, serialize_task
from ..state import _state_proxy as _state


def api_get_tasks(handler, params) -> None:
    """GET /api/tasks — list all tasks."""
    _state.refresh()
    view = params.get("view", ["pending"])[0]

    if view == "all":
        items = get_all_tasks_flat(_state.tasks_by_date)
    else:
        items = get_pending_tasks(_state.tasks_by_date)

    json_response(handler, {
        "tasks": [serialize_task(item) for item in items],
        "id_width": get_id_width(_state.tasks_by_date),
        "states": VALID_STATES,
        "priorities": VALID_PRIORITIES,
    })


def api_get_agenda(handler, params) -> None:
    """GET /api/agenda — agenda view data."""
    _state.refresh()
    days = int(params.get("days", ["7"])[0])
    data = get_agenda_data(_state.tasks_by_date, days)

    json_response(handler, {
        "overdue": [serialize_task(t) for t in data.overdue],
        "due_today": [serialize_task(t) for t in data.due_today],
        "due_soon": [serialize_task(t) for t in data.due_soon],
        "days_ahead": data.days_ahead,
    })


def api_get_calendar(handler, params) -> None:
    """GET /api/calendar — calendar view data."""
    _state.refresh()

    view = params.get("view", ["month"])[0]
    year = int(params.get("year", [0])[0]) or None
    month = int(params.get("month", [0])[0]) or None
    day = int(params.get("day", [0])[0]) or None

    data = get_calendar_data(_state.tasks_by_date, view=view, year=year, month=month, day=day)

    days_serialized = {}
    for date_str, tasks in data.days.items():
        days_serialized[date_str] = [serialize_task(t) for t in tasks]

    json_response(handler, {
        "view": data.view,
        "year": data.year,
        "month": data.month,
        "start_date": data.start_date,
        "end_date": data.end_date,
        "days": days_serialized,
    })


def api_get_kanban(handler, params) -> None:
    """GET /api/kanban?tag=<tag> — kanban board data with optional tag filter."""
    _state.refresh()
    tag_filter = params.get("tag", [None])[0]
    data = get_kanban_data(_state.tasks_by_date, tag_filter=tag_filter)

    columns = {}
    for col in data.columns:
        columns[col] = [serialize_task(t) for t in data.column_tasks[col]]

    json_response(handler, {"columns": data.columns, "tasks": columns, "tag": tag_filter})


def api_get_stats(handler, params) -> None:
    """GET /api/stats — statistics."""
    _state.refresh()
    data = get_stats_data(_state.tasks_by_date)

    json_response(handler, {
        "total": data.total,
        "by_state": data.by_state,
        "by_priority": data.by_priority,
        "overdue": data.overdue_count,
        "due_today": data.due_today_count,
        "due_this_week": data.due_this_week_count,
    })


def api_get_weekly_report(handler, params) -> None:
    """GET /api/weekly — weekly report data."""
    _state.refresh()
    days = int(params.get("days", ["7"])[0])
    data = get_weekly_report_data(_state.tasks_by_date, days)

    json_response(handler, {
        "days": data.days,
        "period_start": data.period_start,
        "period_end": data.period_end,
        "completed": [serialize_task(t) for t in data.completed],
        "in_progress": [serialize_task(t) for t in data.in_progress],
        "upcoming": [serialize_task(t) for t in data.upcoming],
        "total": data.total,
        "total_done": data.total_done,
        "total_pending": data.total_pending,
    })


def api_get_burndown(handler, params) -> None:
    """GET /api/burndown — burndown chart data."""
    _state.refresh()
    sprint_days = int(params.get("days", ["14"])[0])
    data = get_burndown_data(_state.tasks_by_date, sprint_days)

    json_response(handler, {
        "sprint_days": data.sprint_days,
        "total_tasks": data.total_tasks,
        "current_remaining": data.current_remaining,
        "ideal_per_day": data.ideal_per_day,
        "velocity": data.velocity,
        "points": [{"date": p.date, "remaining": p.remaining} for p in data.points],
    })


def api_get_tags(handler, params) -> None:
    """GET /api/tags — all tags with counts."""
    _state.refresh()
    tags = get_all_tags_data(_state.tasks_by_date)
    json_response(handler, {"tags": tags})


def api_get_tag_tasks(handler, params) -> None:
    """GET /api/tags/<tag> — tasks for a specific tag."""
    _state.refresh()
    tag = params.get("tag", [None])[0]
    if not tag:
        error_response(handler, "tag parameter required")
        return

    data = get_tag_view_data(_state.tasks_by_date, tag)
    json_response(handler, {
        "tag": data.tag,
        "tasks": [serialize_task(t) for t in data.tasks],
    })


def api_get_blockers(handler, params) -> None:
    """GET /api/blockers — tasks with blocker relationships."""
    _state.refresh()
    data = get_blockers_data(_state.tasks_by_date)

    json_response(handler, {
        "blockers": [
            {
                "task": serialize_task(b.task),
                "blocked_by": b.blocked_by,
                "blocks": b.blocks,
                "is_blocked": b.is_blocked,
            }
            for b in data
        ],
    })


def api_get_time_tracking(handler, params) -> None:
    """GET /api/time — time tracking data."""
    _state.refresh()
    data = get_time_tracking_data(_state.tasks_by_date)

    from src.tm_features import format_time_spent, get_total_time_spent
    total_minutes = get_total_time_spent(_state.tasks_by_date)

    json_response(handler, {
        "tasks": [
            {
                "task": serialize_task(item.task),
                "minutes": item.minutes_spent,
                "formatted": item.formatted,
            }
            for item in data
        ],
        "total_minutes": total_minutes,
        "total_formatted": format_time_spent(total_minutes) if total_minutes > 0 else "0m",
    })


def api_search_tasks(handler, params) -> None:
    """GET /api/search?q=... — search tasks by title/tag."""
    _state.refresh()
    query = params.get("q", [""])[0].strip().lower()

    if not query:
        json_response(handler, {"tasks": []})
        return

    items = get_all_tasks_flat(_state.tasks_by_date)
    results = []
    for item in items:
        if (query in item.title.lower()
                or any(query in tag.lower() for tag in item.tags)
                or any(query in n.lower() for n in item.notes)):
            results.append(serialize_task(item))

    # Also search notes
    from src.tm_notes import search_notes
    notes = search_notes(_state.journal_path, query)

    json_response(handler, {"tasks": results, "notes": notes, "query": query})


def api_get_templates(handler, params) -> None:
    """GET /api/templates — list saved templates."""
    from src.tm_features import get_templates
    templates = get_templates()
    items = []
    for name, data in templates.items():
        items.append({
            "name": name,
            "title": data.get("title", ""),
            "state": data.get("state", ""),
            "priority": data.get("priority", ""),
            "subtasks": data.get("subtasks", []),
            "recurrence": data.get("recurrence", ""),
        })
    json_response(handler, {"templates": items})


def api_save_template(handler, params) -> None:
    """POST /api/templates/save — save current task as template."""
    from src.tm_features import save_template
    from ..serializers import read_body
    body = read_body(handler)
    name = body.get("name", "").strip()
    if not name:
        error_response(handler, "Template name is required")
        return
    data = {
        "title": body.get("title", ""),
        "state": body.get("state", ""),
        "priority": body.get("priority", ""),
        "subtasks": body.get("subtasks", []),
        "recurrence": body.get("recurrence", ""),
    }
    if save_template(name, data):
        json_response(handler, {"ok": True})
    else:
        error_response(handler, "Could not save template")


def api_apply_template(handler, params) -> None:
    """POST /api/templates/apply — create task from template."""
    from src.tm_features import get_template
    from src.tm_journal import add_task_to_file, add_subtask_to_task
    from src.tm_journal import read_journal_snapshot, write_journal
    from src.tm_config import DEFAULT_STATE
    from ..serializers import read_body
    body = read_body(handler)
    name = body.get("name", "").strip()
    if not name:
        error_response(handler, "Template name is required")
        return
    tpl = get_template(name)
    if not tpl:
        error_response(handler, f"Template '{name}' not found")
        return
    snapshot = read_journal_snapshot(_state.journal_path)
    title = tpl.get("title", name)
    state = tpl.get("state", DEFAULT_STATE)
    priority = tpl.get("priority")
    recurrence = tpl.get("recurrence")
    _state.refresh()
    if add_task_to_file(_state.journal_path, title, state, None, None, priority, recurrence):
        from src.tm_journal import parse_journal
        from src.tm_logic import find_task_by_title_match
        # Add subtasks
        subtasks = tpl.get("subtasks", [])
        if subtasks:
            refreshed = parse_journal(_state.journal_path)
            parent = find_task_by_title_match(refreshed, title)
            if parent:
                for st in subtasks:
                    add_subtask_to_task(_state.journal_path, parent, st, DEFAULT_STATE)
        json_response(handler, {"ok": True, "title": title})
    else:
        error_response(handler, "Could not create task from template")


def api_parse_date(handler, params) -> None:
    """GET /api/parse-date?q=tomorrow — parse natural language date."""
    from src.tm_logic import parse_date_input
    raw = params.get("q", [""])[0].strip()
    if not raw:
        json_response(handler, {"parsed": None, "original": raw})
        return
    dt = parse_date_input(raw)
    if dt:
        parsed = dt.strftime("%d/%m/%Y")
    else:
        parsed = None
    json_response(handler, {"parsed": parsed, "original": raw})


def api_get_gantt(handler, params) -> None:
    """GET /api/gantt — Gantt chart data."""
    _state.refresh()
    data = get_gantt_data(_state.tasks_by_date)

    def fmt(dt):
        return dt.strftime("%d/%m/%Y") if dt else None

    json_response(handler, {
        "tasks": [
            {
                "id": t.task_id,
                "title": t.title,
                "state": t.state,
                "priority": t.priority,
                "start_date": fmt(t.start_date),
                "end_date": fmt(t.end_date),
                "blocked_by": t.blocked_by[:],
                "blocks": t.blocks[:],
            }
            for t in data.tasks
        ],
        "range_start": data.range_start,
        "range_end": data.range_end,
    })
