"""Extended features — re-exports from sub-modules plus recurrence and templates."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

from .tm_config import VALID_RECURRENCES
from .tm_settings import get_setting

# Re-exports from sub-modules
from .tm_blockers import (
    add_blocker_metadata,
    add_blocks_metadata,
    extract_blockers_from_line,
    extract_blocks_from_line,
    find_task_by_title_match,
    is_task_blocked,
    remove_all_blocker_metadata,
    remove_all_blocks_metadata,
    remove_blocker_metadata,
    remove_blocks_metadata,
)
from .tm_export_import import (
    export_to_csv,
    export_to_json,
    export_to_markdown,
    import_from_json,
)
from .tm_kanban import (
    PRIORITY_ORDER,
    STATE_ORDER,
    SUBTASK_DUE_PATTERN,
    extract_subtask_due_date,
    generate_burndown,
    generate_weekly_report,
    get_all_tags,
    get_tasks_by_tag,
    render_kanban,
    sort_tasks,
    subtask_due_display,
)
from .tm_pomodoro import run_pomodoro
from .tm_time_tracking import (
    extract_time_spent_from_line,
    format_time_spent,
    get_total_time_spent,
    parse_time_spent,
    update_time_in_line,
)


# ─── Recurrence ────────────────────────────────────────────────────────────

RECUR_PATTERN = re.compile(r"recur(?:rence)?[:=]\s*(\w+)", re.IGNORECASE)


def parse_recurrence(text: str) -> Optional[str]:
    """Extract recurrence value from task metadata text."""
    match = RECUR_PATTERN.search(text)
    if match:
        value = match.group(1).lower()
        if value in VALID_RECURRENCES:
            return value
    return None


def compute_next_recurrence_date(current_date: datetime, recurrence: str) -> datetime:
    """Compute next occurrence date based on recurrence type."""
    if recurrence == "daily":
        return current_date + timedelta(days=1)
    elif recurrence == "weekly":
        return current_date + timedelta(weeks=1)
    elif recurrence == "biweekly":
        return current_date + timedelta(weeks=2)
    elif recurrence == "monthly":
        month = current_date.month + 1
        year = current_date.year
        if month > 12:
            month = 1
            year += 1
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(current_date.day, max_day)
        return current_date.replace(year=year, month=month, day=day)
    elif recurrence == "yearly":
        try:
            return current_date.replace(year=current_date.year + 1)
        except ValueError:
            return current_date.replace(year=current_date.year + 1, day=28)
    return current_date + timedelta(weeks=1)


def generate_recurring_task_line(task: object, recurrence: str, next_date: datetime) -> Tuple[str, datetime]:
    """Generate the text line for the next recurring instance and its target date."""
    return task.title, next_date


# ─── Templates ─────────────────────────────────────────────────────────────

def get_templates() -> dict:
    """Get all templates from config."""
    return get_setting("templates", {})


def get_template(name: str) -> Optional[dict]:
    """Get a specific template by name (case-insensitive)."""
    templates = get_templates()
    for key, value in templates.items():
        if key.lower() == name.lower():
            return value
    return None


def save_template(name: str, template_data: dict) -> bool:
    """Save a template to config."""
    from .tm_settings import load_settings, _settings_path
    settings = load_settings()
    if "templates" not in settings:
        settings["templates"] = {}
    settings["templates"][name] = template_data
    target = _settings_path or Path(__file__).parent / ".ttm_config"
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError:
        return False


def delete_template(name: str) -> bool:
    """Delete a template from config."""
    from .tm_settings import load_settings, _settings_path
    settings = load_settings()
    templates = settings.get("templates", {})
    key_to_delete = None
    for key in templates:
        if key.lower() == name.lower():
            key_to_delete = key
            break
    if key_to_delete is None:
        return False
    del templates[key_to_delete]
    settings["templates"] = templates
    target = _settings_path or Path(__file__).parent / ".ttm_config"
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError:
        return False
