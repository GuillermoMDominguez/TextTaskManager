"""Kanban view, project/tag view, weekly report, burndown chart, and task sorting."""

import os
import re
import shutil
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from .tm_config import FINISHED_STATES, PROGRESS_STATES, VALID_STATES, VALID_RECURRENCES
from .tm_logic import get_id_width
from .tm_models import Subtask, Task
from .tm_settings import get_setting

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

def _task_has_tag(task: Task, tag: str) -> bool:
    """Return whether a task or one of its subtasks contains tag."""
    tag_lower = tag.lstrip("#").lower()
    task_tags = [t.lower() for t in task.get_tags()]
    subtask_tags = [t.lower() for subtask in task.subtasks for t in subtask.get_tags()]
    return tag_lower in task_tags or tag_lower in subtask_tags


def render_kanban(
    tasks_by_date: dict,
    columns: Optional[List[str]] = None,
    tag_filter: Optional[str] = None,
    search_query: Optional[str] = None,
) -> str:
    """Render a kanban board as a string for terminal output.

    Args:
        tasks_by_date: Dictionary of tasks by date
        columns: Optional list of column names
        tag_filter: Optional tag to filter tasks by (without # prefix) - legacy, use search_query
        search_query: Optional search query (supports #tag, priority:X, due:X, free text)
    """
    from .tm_ui import Colors, get_state_color
    from .tm_logic import task_matches_search

    if columns is None:
        columns = get_setting("kanban_columns", ["BACKLOG", "IN PROGRESS", "TESTING", "DONE"])

    column_tasks: dict = {col: [] for col in columns}
    for tasks in tasks_by_date.values():
        for task in tasks:
            if tag_filter and not _task_has_tag(task, tag_filter):
                continue
            if search_query and not task_matches_search(task, search_query):
                continue
            if task.state in column_tasks:
                column_tasks[task.state].append(task)

    try:
        term_width = os.get_terminal_size().columns
    except OSError:
        term_width = 120

    num_cols = len(columns)
    col_width = max(20, (term_width - (num_cols + 1)) // num_cols)
    reset = Colors.RESET
    id_width = get_id_width(tasks_by_date)

    lines: List[str] = []

    header_cells = []
    for col in columns:
        color = get_state_color(col)
        padded = col.center(col_width)
        header_cells.append(f"{color}{padded}{reset}")
    lines.append("┌" + "┬".join("─" * col_width for _ in columns) + "┐")
    lines.append("│" + "│".join(header_cells) + "│")
    lines.append("├" + "┼".join("─" * col_width for _ in columns) + "┤")

    max_rows = max(len(tasks) for tasks in column_tasks.values()) if column_tasks else 0

    for row_idx in range(max_rows):
        cells = []
        for col in columns:
            tasks = column_tasks[col]
            color = get_state_color(col)
            if row_idx < len(tasks):
                task = tasks[row_idx]
                task_id = task.task_id or "?"
                title = task.title
                priority_badge = f"[{task.priority[0]}]" if task.priority else ""
                id_value = task_id.zfill(id_width) if task_id.isdigit() else task_id
                cell_content = f"[{id_value}]{priority_badge} {title}"
                if len(cell_content) > col_width - 2:
                    cell_content = cell_content[: col_width - 3] + "~"
                cells.append(f" {color}{cell_content.ljust(col_width - 1)}{reset}")
            else:
                cells.append(" " * col_width)
        lines.append("│" + "│".join(cells) + "│")

    lines.append("└" + "┴".join("─" * col_width for _ in columns) + "┘")

    for col in columns:
        count = len(column_tasks[col])
        color = get_state_color(col)
        lines.append(f"  {color}{col}{reset}: {count} task(s)")

    return "\n".join(lines)


# ─── Weekly Report ─────────────────────────────────────────────────────────

def generate_weekly_report(tasks_by_date: dict, days: int = 7, end_date: Optional[datetime] = None) -> str:
    """Generate a weekly summary of completed tasks and current status.

    Args:
        tasks_by_date: all tasks grouped by date
        days: number of days to look back from end_date (default 7)
        end_date: end of the report period (defaults to today)
    """
    from .tm_ui import Colors, get_state_color

    today = (end_date or datetime.now()).date()
    period_start = today - timedelta(days=days)

    completed: List[Task] = []
    in_progress: List[Task] = []
    upcoming: List[Task] = []

    for date, tasks in tasks_by_date.items():
        for task in tasks:
            if task.is_finished():
                done_date = task.done_date.date() if task.done_date else (date.date() if date else None)
                if done_date and period_start <= done_date <= today:
                    completed.append(task)
            elif task.state in PROGRESS_STATES:
                in_progress.append(task)
            elif task.due_date and task.due_date.date() <= today + timedelta(days=7):
                upcoming.append(task)

    tw = shutil.get_terminal_size((80, 24)).columns
    r = Colors.RESET
    lines: List[str] = []

    title = f" Weekly Report ({period_start.strftime('%d/%m/%Y')} – {today.strftime('%d/%m/%Y')}) "
    lines.append(f"{Colors.BOLD}{'─' * 3}{title}{'─' * max(0, tw - len(title) - 3)}{r}")

    done_color = get_state_color(FINISHED_STATES[0])
    lines.append(f"\n  {done_color}{Colors.BOLD}✓ COMPLETED ({len(completed)}){r}")
    lines.append(f"  {Colors.DIM}{'─' * (tw - 4)}{r}")
    if completed:
        for task in completed:
            priority = f" [{task.priority}]" if task.priority else ""
            lines.append(f"    {done_color}•{r} {task.title}{Colors.DIM}{priority}{r}")
    else:
        lines.append(f"    {Colors.DIM}(none){r}")

    ip_color = get_state_color(PROGRESS_STATES[0])
    lines.append(f"\n  {ip_color}{Colors.BOLD}⚡ IN PROGRESS ({len(in_progress)}){r}")
    lines.append(f"  {Colors.DIM}{'─' * (tw - 4)}{r}")
    if in_progress:
        for task in in_progress:
            due = f" [DUE:{task.due_date.strftime('%d/%m/%Y')}]" if task.due_date else ""
            lines.append(f"    {ip_color}•{r} {task.title}{Colors.DIM}{due}{r}")
    else:
        lines.append(f"    {Colors.DIM}(none){r}")

    lines.append(f"\n  {Colors.DATE}{Colors.BOLD}📅 UPCOMING DUE ({len(upcoming)}){r}")
    lines.append(f"  {Colors.DIM}{'─' * (tw - 4)}{r}")
    if upcoming:
        upcoming_sorted = sorted(upcoming, key=lambda t: t.due_date or datetime.max)
        for task in upcoming_sorted:
            due = task.due_date.strftime('%d/%m/%Y') if task.due_date else ""
            state_color = get_state_color(task.state)
            lines.append(f"    • {task.title} {state_color}{task.state}{r}{Colors.DIM} [DUE:{due}]{r}")
    else:
        lines.append(f"    {Colors.DIM}(none){r}")

    total = sum(len(tasks) for tasks in tasks_by_date.values())
    total_done = sum(1 for tasks in tasks_by_date.values() for t in tasks if t.is_finished())
    total_pending = total - total_done
    lines.append(f"\n  {Colors.DIM}{'─' * (tw - 4)}{r}")
    lines.append(f"  Summary: {total} total │ {done_color}{total_done} done{r} │ {total_pending} pending")

    return "\n".join(lines)


# ─── Burndown Chart ────────────────────────────────────────────────────────

def generate_burndown(tasks_by_date: dict, sprint_days: int = 14) -> str:
    """Generate an ASCII burndown chart showing tasks completed vs remaining over time."""
    today = datetime.now().date()
    start_date = today - timedelta(days=sprint_days - 1)

    all_tasks: List[Task] = []
    for tasks in tasks_by_date.values():
        all_tasks.extend(tasks)

    total_tasks = len(all_tasks)
    if total_tasks == 0:
        return "No tasks to chart."

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

    tw = shutil.get_terminal_size((80, 24)).columns
    chart_width = min(tw - 12, sprint_days * 4, 60)
    max_val = total_tasks
    lines: List[str] = []

    lines.append(f"Burndown ({sprint_days} days) — {total_tasks} total tasks")
    lines.append("─" * (chart_width + 10))

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

    lines.append(f"    └{'─' * len(daily_remaining)}")
    if daily_remaining:
        first = daily_remaining[0][0]
        last = daily_remaining[-1][0]
        mid_idx = len(daily_remaining) // 2
        mid = daily_remaining[mid_idx][0]
        axis = f"     {first}" + " " * max(0, mid_idx - len(first) - 1) + mid
        axis += " " * max(0, len(daily_remaining) - len(axis) + 5 - len(last)) + last
        lines.append(axis)

    lines.append("")
    ideal_per_day = total_tasks / max(sprint_days - 1, 1)
    current_remaining = daily_remaining[-1][1] if daily_remaining else total_tasks
    lines.append(f"  Ideal: -{ideal_per_day:.1f}/day | Current remaining: {current_remaining} | Velocity: {total_tasks - current_remaining} done")

    return "\n".join(lines)


# ─── Subtask due date helpers ──────────────────────────────────────────────

SUBTASK_DUE_PATTERN = re.compile(r"\[due=(\d{1,2}/\d{1,2}/\d{2,4})\]", re.IGNORECASE)


def extract_subtask_due_date(title: str) -> Optional[datetime]:
    """Extract inline due date from subtask title like [due=10/06/2026] or [due=10/06/26]."""
    match = SUBTASK_DUE_PATTERN.search(title)
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y")
        except ValueError:
            pass
        try:
            return datetime.strptime(match.group(1), "%d/%m/%y")
        except ValueError:
            pass
    return None


def subtask_due_display(subtask: Subtask) -> Optional[str]:
    """Return due date string if subtask has one embedded in title."""
    due = extract_subtask_due_date(subtask.title)
    if due:
        return due.strftime("%d/%m/%Y")
    return None
