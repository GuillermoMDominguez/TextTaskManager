"""Extended features: export/import, kanban, project view, weekly report, recurring tasks."""

import csv
import io
import json
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

    lines: List[str] = []
    lines.append(f"Weekly Report ({period_start.strftime('%d/%m/%Y')} - {today.strftime('%d/%m/%Y')})")
    lines.append("=" * 60)

    lines.append(f"\n✓ COMPLETED ({len(completed)})")
    lines.append("-" * 40)
    if completed:
        for task in completed:
            priority = f" [{task.priority}]" if task.priority else ""
            lines.append(f"  • {task.title}{priority}")
    else:
        lines.append("  (none)")

    lines.append(f"\n⚡ IN PROGRESS ({len(in_progress)})")
    lines.append("-" * 40)
    if in_progress:
        for task in in_progress:
            due = f" (due: {task.due_date.strftime('%d/%m/%Y')})" if task.due_date else ""
            lines.append(f"  • {task.title}{due}")
    else:
        lines.append("  (none)")

    lines.append(f"\n📅 UPCOMING DUE ({len(upcoming)})")
    lines.append("-" * 40)
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
    lines.append(f"\n{'─' * 60}")
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
