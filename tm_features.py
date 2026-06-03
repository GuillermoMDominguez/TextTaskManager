"""Extended features: export/import, kanban, project view, weekly report, recurring tasks."""

import csv
import io
import json
import shutil
import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from tm_config import VALID_STATES, FINISHED_STATES
from tm_models import Task, Subtask
from tm_settings import get_setting


# ─── Recurrence ────────────────────────────────────────────────────────────

RECUR_PATTERN = re.compile(r"recur(?:rence)?[:=]\s*(\w+)", re.IGNORECASE)
VALID_RECURRENCES = ("daily", "weekly", "biweekly", "monthly", "yearly")


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
        day = min(current_date.day, 28)
        return current_date.replace(year=year, month=month, day=day)
    elif recurrence == "yearly":
        return current_date.replace(year=current_date.year + 1)
    return current_date + timedelta(weeks=1)


def generate_recurring_task_line(task: Task, recurrence: str, next_date: datetime) -> Tuple[str, datetime]:
    """Generate the text line for the next recurring instance and its target date."""
    return task.title, next_date


# ─── Sorting ───────────────────────────────────────────────────────────────

PRIORITY_ORDER = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
STATE_ORDER = {state: idx for idx, state in enumerate(VALID_STATES)}


def sort_tasks(tasks: List[Task], sort_by: str = "none", direction: str = "asc") -> List[Task]:
    """Sort a list of tasks by the given criterion."""
    if sort_by == "none" or not tasks:
        return tasks

    reverse = direction.lower() == "desc"

    if sort_by == "priority":
        return sorted(
            tasks,
            key=lambda t: PRIORITY_ORDER.get(t.priority or "LOW", 3),
            reverse=reverse,
        )
    elif sort_by == "due_date":
        no_due = datetime.max if not reverse else datetime.min
        return sorted(
            tasks,
            key=lambda t: t.due_date or no_due,
            reverse=reverse,
        )
    elif sort_by == "state":
        return sorted(
            tasks,
            key=lambda t: STATE_ORDER.get(t.state, 99),
            reverse=reverse,
        )
    return tasks


# ─── Project/Tag View ──────────────────────────────────────────────────────

def get_tasks_by_tag(tasks_by_date: dict, tag: str) -> List[Task]:
    """Return all tasks (across all dates) that contain the given tag."""
    tag_lower = tag.lstrip("#").lower()
    results: List[Task] = []
    for tasks in tasks_by_date.values():
        for task in tasks:
            all_tags = task.get_tags()
            for subtask in task.subtasks:
                all_tags.extend(subtask.get_tags())
            if tag_lower in all_tags:
                results.append(task)
    return results


def get_all_tags(tasks_by_date: dict) -> dict:
    """Return dict of tag -> count across all tasks."""
    tag_counts: dict = {}
    for tasks in tasks_by_date.values():
        for task in tasks:
            for tag in task.get_tags():
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            for subtask in task.subtasks:
                for tag in subtask.get_tags():
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return tag_counts


# ─── Kanban View ───────────────────────────────────────────────────────────

def render_kanban(tasks_by_date: dict, columns: Optional[List[str]] = None) -> str:
    """Render a kanban board as a string for terminal output."""
    if columns is None:
        columns = get_setting("kanban_columns", ["BACKLOG", "IN PROGRESS", "TESTING", "DONE"])

    # Collect all tasks into columns
    column_tasks: dict = {col: [] for col in columns}
    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.state in column_tasks:
                column_tasks[task.state].append(task)

    # Calculate column width based on terminal
    try:
        term_width = os.get_terminal_size().columns
    except OSError:
        term_width = 120

    num_cols = len(columns)
    col_width = max(20, (term_width - (num_cols + 1)) // num_cols)

    lines: List[str] = []

    # Header
    header = "│".join(col.center(col_width) for col in columns)
    separator = "┼".join("─" * col_width for _ in columns)
    lines.append("┌" + "┬".join("─" * col_width for _ in columns) + "┐")
    lines.append("│" + header + "│")
    lines.append("├" + separator + "┤")

    # Find max rows needed
    max_rows = max(len(tasks) for tasks in column_tasks.values()) if column_tasks else 0

    for row_idx in range(max_rows):
        cells = []
        for col in columns:
            tasks = column_tasks[col]
            if row_idx < len(tasks):
                task = tasks[row_idx]
                task_id = task.task_id or "?"
                title = task.title
                priority_badge = f"[{task.priority[0]}]" if task.priority else ""
                cell_content = f"[{task_id}]{priority_badge} {title}"
                if len(cell_content) > col_width - 2:
                    cell_content = cell_content[: col_width - 3] + "~"
                cells.append(f" {cell_content.ljust(col_width - 1)}")
            else:
                cells.append(" " * col_width)
        lines.append("│" + "│".join(cells) + "│")

    lines.append("└" + "┴".join("─" * col_width for _ in columns) + "┘")

    # Summary
    for col in columns:
        count = len(column_tasks[col])
        lines.append(f"  {col}: {count} task(s)")

    return "\n".join(lines)


# ─── Export ────────────────────────────────────────────────────────────────

def export_to_json(tasks_by_date: dict) -> str:
    """Export all tasks to JSON format."""
    data = []
    for date, tasks in tasks_by_date.items():
        for task in tasks:
            task_dict = {
                "title": task.title,
                "state": task.state,
                "date": date.strftime("%d/%m/%Y") if date else None,
                "due_date": task.due_date.strftime("%d/%m/%Y") if task.due_date else None,
                "priority": task.priority,
                "tags": task.get_tags(),
                "notes": task.comments,
                "subtasks": [
                    {
                        "title": st.title,
                        "state": st.state,
                        "tags": st.get_tags(),
                    }
                    for st in task.subtasks
                ],
            }
            data.append(task_dict)
    return json.dumps(data, indent=2, ensure_ascii=False)


def export_to_csv(tasks_by_date: dict) -> str:
    """Export all tasks to CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Title", "State", "Date", "Due Date", "Priority", "Tags", "Notes", "Subtasks"])

    for date, tasks in tasks_by_date.items():
        for task in tasks:
            date_str = date.strftime("%d/%m/%Y") if date else ""
            due_str = task.due_date.strftime("%d/%m/%Y") if task.due_date else ""
            tags = ", ".join(f"#{t}" for t in task.get_tags())
            notes = " | ".join(task.comments)
            subtasks = " | ".join(f"{st.title} [{st.state}]" for st in task.subtasks)
            writer.writerow([
                task.task_id or "",
                task.title,
                task.state,
                date_str,
                due_str,
                task.priority or "",
                tags,
                notes,
                subtasks,
            ])

    return output.getvalue()


def export_to_markdown(tasks_by_date: dict) -> str:
    """Export all tasks to Markdown format."""
    lines: List[str] = ["# Task Report", ""]
    lines.append(f"*Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}*")
    lines.append("")

    sorted_dates = sorted([d for d in tasks_by_date.keys() if d is not None], reverse=True)
    if None in tasks_by_date:
        sorted_dates.append(None)

    for date in sorted_dates:
        tasks = tasks_by_date[date]
        if not tasks:
            continue

        date_str = date.strftime("%A, %d/%m/%Y") if date else "No Date"
        lines.append(f"## {date_str}")
        lines.append("")

        for task in tasks:
            priority_badge = f" `{task.priority}`" if task.priority else ""
            due_badge = f" (due: {task.due_date.strftime('%d/%m/%Y')})" if task.due_date else ""
            checkbox = "x" if task.is_finished() else " "
            lines.append(f"- [{checkbox}] **{task.title}** — {task.state}{priority_badge}{due_badge}")

            for comment in task.comments:
                lines.append(f"  - 📝 {comment}")

            for subtask in task.subtasks:
                st_checkbox = "x" if subtask.is_finished() else " "
                lines.append(f"  - [{st_checkbox}] {subtask.title} — {subtask.state}")

        lines.append("")

    return "\n".join(lines)


def import_from_json(json_text: str) -> List[str]:
    """Convert JSON task data into journal lines for appending."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    lines: List[str] = []
    grouped: OrderedDict = OrderedDict()

    for item in data:
        date_str = item.get("date")
        date_key = date_str or "today"
        grouped.setdefault(date_key, []).append(item)

    for date_key, items in grouped.items():
        if date_key == "today":
            lines.append(f"## {datetime.now().strftime('%d/%m/%Y')}\n")
        else:
            lines.append(f"## {date_key}\n")

        for item in items:
            title = item.get("title", "Untitled")
            state = item.get("state", "BACKLOG")
            parts = [f"- {title} -- {state}"]
            if item.get("due_date"):
                parts.append(f"due:{item['due_date']}")
            if item.get("priority"):
                parts.append(f"priority:{item['priority']}")
            lines.append(" -- ".join(parts) + "\n")

            for note in item.get("notes", []):
                lines.append(f": {note}\n")

            for st in item.get("subtasks", []):
                st_title = st.get("title", "")
                st_state = st.get("state", "BACKLOG")
                lines.append(f"+ {st_title} -- {st_state}\n")

    return lines


# ─── Weekly Report ─────────────────────────────────────────────────────────

def generate_weekly_report(tasks_by_date: dict, days: int = 7) -> str:
    """Generate a weekly summary of completed tasks and current status."""
    today = datetime.now().date()
    period_start = today - timedelta(days=days)

    completed: List[Task] = []
    in_progress: List[Task] = []
    upcoming: List[Task] = []

    for date, tasks in tasks_by_date.items():
        for task in tasks:
            if task.is_finished():
                # Count tasks in date sections within the period
                if date and period_start <= date.date() <= today:
                    completed.append(task)
            elif task.state == "IN PROGRESS":
                in_progress.append(task)
            elif task.due_date and task.due_date.date() <= today + timedelta(days=7):
                upcoming.append(task)

    tw = shutil.get_terminal_size((80, 24)).columns
    lines: List[str] = []
    lines.append(f"Weekly Report ({period_start.strftime('%d/%m/%Y')} - {today.strftime('%d/%m/%Y')})")
    lines.append("=" * tw)

    lines.append(f"\n✓ COMPLETED ({len(completed)})")
    lines.append("-" * tw)
    if completed:
        for task in completed:
            priority = f" [{task.priority}]" if task.priority else ""
            lines.append(f"  • {task.title}{priority}")
    else:
        lines.append("  (none)")

    lines.append(f"\n⚡ IN PROGRESS ({len(in_progress)})")
    lines.append("-" * tw)
    if in_progress:
        for task in in_progress:
            due = f" (due: {task.due_date.strftime('%d/%m/%Y')})" if task.due_date else ""
            lines.append(f"  • {task.title}{due}")
    else:
        lines.append("  (none)")

    lines.append(f"\n📅 UPCOMING DUE ({len(upcoming)})")
    lines.append("-" * tw)
    if upcoming:
        upcoming_sorted = sorted(upcoming, key=lambda t: t.due_date or datetime.max)
        for task in upcoming_sorted:
            due = task.due_date.strftime('%d/%m/%Y') if task.due_date else ""
            lines.append(f"  • {task.title} (due: {due})")
    else:
        lines.append("  (none)")

    # Stats summary
    total = sum(len(tasks) for tasks in tasks_by_date.values())
    total_done = sum(1 for tasks in tasks_by_date.values() for t in tasks if t.is_finished())
    total_pending = total - total_done
    lines.append(f"\n{'─' * tw}")
    lines.append(f"Summary: {total} total | {total_done} done | {total_pending} pending")

    return "\n".join(lines)


# ─── Subtask due date helpers ──────────────────────────────────────────────

SUBTASK_DUE_PATTERN = re.compile(r"\[due=(\d{1,2}/\d{1,2}/\d{4})\]", re.IGNORECASE)


def extract_subtask_due_date(title: str) -> Optional[datetime]:
    """Extract inline due date from subtask title like [due=10/06/2026]."""
    match = SUBTASK_DUE_PATTERN.search(title)
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y")
        except ValueError:
            pass
    return None


def subtask_due_display(subtask: Subtask) -> Optional[str]:
    """Return due date string if subtask has one embedded in title."""
    due = extract_subtask_due_date(subtask.title)
    if due:
        return due.strftime("%d/%m/%Y")
    return None


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
    from tm_settings import load_settings, save_settings, _settings_path
    settings = load_settings()
    if "templates" not in settings:
        settings["templates"] = {}
    settings["templates"][name] = template_data
    target = _settings_path or Path(__file__).parent / ".ttm_config"
    try:
        import json as _json
        with open(target, "w", encoding="utf-8") as f:
            _json.dump(settings, f, indent=2)
        return True
    except OSError:
        return False


def delete_template(name: str) -> bool:
    """Delete a template from config."""
    from tm_settings import load_settings, _settings_path
    settings = load_settings()
    templates = settings.get("templates", {})
    # Case-insensitive find
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
        import json as _json
        with open(target, "w", encoding="utf-8") as f:
            _json.dump(settings, f, indent=2)
        return True
    except OSError:
        return False


# ─── Time Tracking ─────────────────────────────────────────────────────────

# Time metadata stored as: -- spent:2h30m
# Format: Xh, Xm, XhYm (hours and minutes)

_TIME_PATTERN = re.compile(r"(\d+)h(?:(\d+)m)?|(\d+)m")


def parse_time_spent(raw: str) -> Optional[int]:
    """Parse time string like '2h', '30m', '1h30m' into total minutes."""
    raw = raw.strip().lower()
    match = re.fullmatch(r"(\d+)h(\d+)m", raw)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = re.fullmatch(r"(\d+)h", raw)
    if match:
        return int(match.group(1)) * 60
    match = re.fullmatch(r"(\d+)m", raw)
    if match:
        return int(match.group(1))
    return None


def format_time_spent(minutes: int) -> str:
    """Format total minutes as XhYm string."""
    if minutes <= 0:
        return "0m"
    h = minutes // 60
    m = minutes % 60
    if h and m:
        return f"{h}h{m}m"
    elif h:
        return f"{h}h"
    return f"{m}m"


def extract_time_spent_from_line(line: str) -> Optional[int]:
    """Extract spent:XhYm from a task line, return minutes or None."""
    match = re.search(r"--\s*(?:spent|time)\s*[:=]\s*(\S+)", line, re.IGNORECASE)
    if match:
        return parse_time_spent(match.group(1))
    return None


def update_time_in_line(line: str, total_minutes: int) -> str:
    """Update or insert spent:XhYm metadata in a task line."""
    time_str = format_time_spent(total_minutes)
    # Replace existing
    new_line, count = re.subn(
        r"--\s*(?:spent|time)\s*[:=]\s*\S+",
        f"-- spent:{time_str}",
        line,
        count=1,
        flags=re.IGNORECASE,
    )
    if count:
        return new_line
    # Append
    return line.rstrip() + f" -- spent:{time_str}"


def get_total_time_spent(tasks_by_date: dict) -> int:
    """Sum all time spent across all tasks in minutes."""
    total = 0
    for tasks in tasks_by_date.values():
        for task in tasks:
            if getattr(task, "time_spent", None):
                total += task.time_spent
    return total


# ─── Task Dependencies / Blockers ──────────────────────────────────────────

# Stored as: -- blocks:title_hash or -- blockedby:title_hash
# title_hash = first 8 chars of a simple hash of the blocker title

def _title_hash(title: str) -> str:
    """Generate a short stable hash from a task title."""
    import hashlib
    return hashlib.md5(title.strip().lower().encode()).hexdigest()[:8]


def extract_blockers_from_line(line: str) -> List[str]:
    """Extract blockedby:hash values from a task line."""
    return re.findall(r"--\s*blockedby\s*[:=]\s*(\S+)", line, re.IGNORECASE)


def extract_blocks_from_line(line: str) -> List[str]:
    """Extract blocks:hash values from a task line."""
    return re.findall(r"--\s*blocks\s*[:=]\s*(\S+)", line, re.IGNORECASE)


def add_blocker_metadata(line: str, blocker_title: str) -> str:
    """Add blockedby:hash to a task line."""
    h = _title_hash(blocker_title)
    return line.rstrip() + f" -- blockedby:{h}"


def add_blocks_metadata(line: str, blocked_title: str) -> str:
    """Add blocks:hash to a task line."""
    h = _title_hash(blocked_title)
    return line.rstrip() + f" -- blocks:{h}"


def find_task_by_title_hash(tasks_by_date: dict, title_hash: str) -> Optional[Task]:
    """Find a task whose title matches the given hash prefix."""
    for tasks in tasks_by_date.values():
        for task in tasks:
            if _title_hash(task.title) == title_hash:
                return task
    return None


def is_task_blocked(task: Task, tasks_by_date: dict) -> bool:
    """Check if a task has unresolved blockers."""
    if not hasattr(task, "_raw_line"):
        return False
    blockers = extract_blockers_from_line(getattr(task, "_raw_line", ""))
    if not blockers:
        return False
    for bh in blockers:
        blocker = find_task_by_title_hash(tasks_by_date, bh)
        if blocker and not blocker.is_finished():
            return True
    return False


# ─── Pomodoro Timer ────────────────────────────────────────────────────────

import sys
import time
import select


def run_pomodoro(minutes: int = 25, task_title: str = "") -> int:
    """Run a pomodoro countdown in the terminal. Returns elapsed minutes.

    Can be interrupted with Enter or Ctrl+C (counts partial time).
    """
    total_seconds = minutes * 60
    label = f" [{task_title[:30]}]" if task_title else ""
    print(f"\n  Pomodoro started: {minutes}min{label}")
    print(f"  Press Enter to stop early.\n")

    start = time.time()
    try:
        while True:
            elapsed = time.time() - start
            remaining = total_seconds - int(elapsed)
            if remaining <= 0:
                break
            mins, secs = divmod(remaining, 60)
            sys.stdout.write(f"\r  ⏱  {mins:02d}:{secs:02d} remaining  ")
            sys.stdout.flush()

            # Check if user pressed Enter (non-blocking)
            if sys.stdin in select.select([sys.stdin], [], [], 1.0)[0]:
                sys.stdin.readline()
                break
    except KeyboardInterrupt:
        pass

    elapsed_minutes = max(1, int((time.time() - start) / 60 + 0.5))
    sys.stdout.write(f"\r  ✓  Pomodoro done! ({elapsed_minutes}min logged)        \n")
    sys.stdout.flush()
    return elapsed_minutes


# ─── Burndown Chart ────────────────────────────────────────────────────────

def generate_burndown(tasks_by_date: dict, sprint_days: int = 14) -> str:
    """Generate an ASCII burndown chart showing tasks completed vs remaining over time."""
    today = datetime.now().date()
    start_date = today - timedelta(days=sprint_days - 1)

    # Count tasks created on or before each day, and completed on each day
    all_tasks: List[Task] = []
    for tasks in tasks_by_date.values():
        all_tasks.extend(tasks)

    total_tasks = len(all_tasks)
    if total_tasks == 0:
        return "No tasks to chart."

    # Build daily remaining count
    # We approximate: tasks in DONE/CANCELLED with a date <= day are "done by that day"
    daily_remaining: List[Tuple[str, int]] = []

    for day_offset in range(sprint_days):
        current_date = start_date + timedelta(days=day_offset)
        done_by_date = 0
        for task in all_tasks:
            if task.is_finished():
                task_date = task.date.date() if task.date else today
                if task_date <= current_date:
                    done_by_date += 1
        remaining = total_tasks - done_by_date
        label = current_date.strftime("%d/%m")
        daily_remaining.append((label, remaining))

    # Render ASCII chart
    tw = shutil.get_terminal_size((80, 24)).columns
    chart_width = min(tw - 12, sprint_days * 4, 60)
    max_val = total_tasks
    lines: List[str] = []

    lines.append(f"Burndown ({sprint_days} days) — {total_tasks} total tasks")
    lines.append("─" * (chart_width + 10))

    # Chart rows (top to bottom: max_val down to 0)
    chart_height = min(15, max_val)
    if chart_height == 0:
        chart_height = 1

    for row in range(chart_height, -1, -1):
        threshold = (row / chart_height) * max_val
        label = f"{int(threshold):>3} │"
        bar = ""
        for _, remaining in daily_remaining:
            if remaining >= threshold:
                bar += "█"
            else:
                bar += " "
        lines.append(f"{label}{bar}")

    # X-axis
    lines.append(f"    └{'─' * len(daily_remaining)}")
    # Date labels (show first, middle, last)
    if daily_remaining:
        first = daily_remaining[0][0]
        last = daily_remaining[-1][0]
        mid_idx = len(daily_remaining) // 2
        mid = daily_remaining[mid_idx][0]
        axis = f"     {first}" + " " * max(0, mid_idx - len(first) - 1) + mid
        axis += " " * max(0, len(daily_remaining) - len(axis) + 5 - len(last)) + last
        lines.append(axis)

    # Ideal burndown line info
    lines.append("")
    ideal_per_day = total_tasks / max(sprint_days - 1, 1)
    current_remaining = daily_remaining[-1][1] if daily_remaining else total_tasks
    lines.append(f"  Ideal: -{ideal_per_day:.1f}/day | Current remaining: {current_remaining} | Velocity: {total_tasks - current_remaining} done")

    return "\n".join(lines)
