"""Pure data functions for views — no print(), no ANSI, no terminal dependencies.

These functions extract and structure the data that each view needs.
Any frontend (CLI, TUI, web) can import this module directly.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .tm_logic import get_id_width
from .tm_models import Task


# ─── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TaskViewItem:
    """A single task as seen by a view."""
    task_id: str
    title: str
    state: str
    priority: Optional[str] = None
    due_date: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    subtasks: List["SubtaskViewItem"] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    time_spent: Optional[str] = None
    jira_key: Optional[str] = None


@dataclass
class SubtaskViewItem:
    """A subtask as seen by a view."""
    task_id: str
    title: str
    state: str
    priority: Optional[str] = None
    due_date: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class AgendaData:
    """Structured agenda data."""
    overdue: List[TaskViewItem]
    due_today: List[TaskViewItem]
    due_soon: List[TaskViewItem]
    days_ahead: int
    id_width: int


@dataclass
class KanbanData:
    """Structured kanban board data."""
    columns: List[str]
    column_tasks: Dict[str, List[TaskViewItem]]
    id_width: int


@dataclass
class StatsData:
    """Structured statistics data."""
    total: int
    by_state: Dict[str, int]
    by_priority: Dict[str, int]
    overdue_count: int
    due_today_count: int
    due_this_week_count: int


@dataclass
class TagViewData:
    """Tasks filtered by a specific tag."""
    tag: str
    tasks: List[TaskViewItem]
    id_width: int


@dataclass
class WeeklyReportData:
    """Weekly report structured data."""
    days: int
    completed: List[TaskViewItem]
    in_progress: List[TaskViewItem]
    upcoming: List[TaskViewItem]
    total: int
    total_done: int
    total_pending: int
    period_start: str
    period_end: str


@dataclass
class BurndownPoint:
    """A single point on the burndown chart."""
    date: str
    remaining: int


@dataclass
class BurndownData:
    """Burndown chart structured data."""
    sprint_days: int
    total_tasks: int
    current_remaining: int
    ideal_per_day: float
    velocity: int
    points: List[BurndownPoint]


@dataclass
class BlockerInfo:
    """A blocker relationship."""
    task: TaskViewItem
    blocked_by: List[str]  # titles of blocking tasks
    blocks: List[str]      # titles of tasks it blocks
    is_blocked: bool


@dataclass
class TimeTrackingItem:
    """Time tracking entry for a task."""
    task: TaskViewItem
    minutes_spent: int
    formatted: str


# ─── Conversion helpers ────────────────────────────────────────────────────────

def _task_to_view_item(task: Task) -> TaskViewItem:
    """Convert a Task model to a TaskViewItem."""
    return TaskViewItem(
        task_id=task.task_id or "?",
        title=task.title,
        state=task.state,
        priority=task.priority,
        due_date=task.due_date,
        tags=task.get_tags(),
        subtasks=[
            SubtaskViewItem(
                task_id=st.task_id or "?",
                title=st.title,
                state=st.state,
                priority=getattr(st, "priority", None),
                due_date=st.due_date,
                tags=st.get_tags(),
                notes=st.comments or [],
            )
            for st in task.subtasks
        ],
        notes=task.comments,
        time_spent=getattr(task, "time_spent", None),
        jira_key=getattr(task, "jira_key", None),
    )


# ─── View data functions ───────────────────────────────────────────────────────

def get_agenda_data(tasks_by_date: dict, days_ahead: int = 7) -> AgendaData:
    """Compute agenda data: overdue, due today, due soon."""
    today = datetime.now().date()
    week_limit = today + timedelta(days=days_ahead)

    overdue: List[TaskViewItem] = []
    due_today: List[TaskViewItem] = []
    due_soon: List[TaskViewItem] = []

    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.is_finished() or task.due_date is None:
                continue
            due = task.due_date.date() if isinstance(task.due_date, datetime) else task.due_date
            item = _task_to_view_item(task)
            if due < today:
                overdue.append(item)
            elif due == today:
                due_today.append(item)
            elif due <= week_limit:
                due_soon.append(item)

    # Sort each group by due date
    for group in (overdue, due_today, due_soon):
        group.sort(key=lambda t: t.due_date or datetime.max)

    return AgendaData(
        overdue=overdue,
        due_today=due_today,
        due_soon=due_soon,
        days_ahead=days_ahead,
        id_width=get_id_width(tasks_by_date),
    )


def get_kanban_data(tasks_by_date: dict, columns: Optional[List[str]] = None) -> KanbanData:
    """Compute kanban board data: tasks grouped by state columns."""
    from .tm_settings import get_setting

    if columns is None:
        columns = get_setting("kanban_columns", ["BACKLOG", "IN PROGRESS", "TESTING", "DONE"])

    column_tasks: Dict[str, List[TaskViewItem]] = {col: [] for col in columns}

    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.state in column_tasks:
                column_tasks[task.state].append(_task_to_view_item(task))

    return KanbanData(
        columns=columns,
        column_tasks=column_tasks,
        id_width=get_id_width(tasks_by_date),
    )


def get_stats_data(tasks_by_date: dict) -> StatsData:
    """Compute task statistics."""
    today = datetime.now().date()
    week_limit = today + timedelta(days=7)

    total = 0
    by_state: Dict[str, int] = {}
    by_priority: Dict[str, int] = {}
    overdue_count = 0
    due_today_count = 0
    due_this_week_count = 0

    for tasks in tasks_by_date.values():
        for task in tasks:
            total += 1
            by_state[task.state] = by_state.get(task.state, 0) + 1
            if task.priority:
                by_priority[task.priority] = by_priority.get(task.priority, 0) + 1

            if task.due_date and not task.is_finished():
                due = task.due_date.date() if isinstance(task.due_date, datetime) else task.due_date
                if due < today:
                    overdue_count += 1
                elif due == today:
                    due_today_count += 1
                elif due <= week_limit:
                    due_this_week_count += 1

    return StatsData(
        total=total,
        by_state=by_state,
        by_priority=by_priority,
        overdue_count=overdue_count,
        due_today_count=due_today_count,
        due_this_week_count=due_this_week_count,
    )


def get_tag_view_data(tasks_by_date: dict, tag: str) -> TagViewData:
    """Get all tasks with a specific tag."""
    from .tm_features import get_tasks_by_tag

    tasks = get_tasks_by_tag(tasks_by_date, tag)
    items = [_task_to_view_item(t) for t in tasks]

    return TagViewData(
        tag=tag,
        tasks=items,
        id_width=get_id_width(tasks_by_date),
    )


def get_all_tasks_flat(tasks_by_date: dict) -> List[TaskViewItem]:
    """Return all tasks as a flat list of view items."""
    items = []
    for tasks in tasks_by_date.values():
        for task in tasks:
            items.append(_task_to_view_item(task))
    return items


def get_pending_tasks(tasks_by_date: dict) -> List[TaskViewItem]:
    """Return only non-finished tasks."""
    items = []
    for tasks in tasks_by_date.values():
        for task in tasks:
            if not task.is_finished():
                items.append(_task_to_view_item(task))
    return items


def get_weekly_report_data(tasks_by_date: dict, days: int = 7) -> WeeklyReportData:
    """Compute weekly report data: completed, in-progress, upcoming tasks."""
    from .tm_config import FINISHED_STATES, PROGRESS_STATES

    today = datetime.now().date()
    period_start = today - timedelta(days=days)

    completed: List[TaskViewItem] = []
    in_progress: List[TaskViewItem] = []
    upcoming: List[TaskViewItem] = []

    for date_key, tasks in tasks_by_date.items():
        for task in tasks:
            if task.is_finished():
                if date_key and period_start <= date_key.date() <= today:
                    completed.append(_task_to_view_item(task))
            elif task.state in PROGRESS_STATES:
                in_progress.append(_task_to_view_item(task))
            elif task.due_date and task.due_date.date() <= today + timedelta(days=7):
                upcoming.append(_task_to_view_item(task))

    total = sum(len(tasks) for tasks in tasks_by_date.values())
    total_done = sum(1 for tasks in tasks_by_date.values() for t in tasks if t.is_finished())

    return WeeklyReportData(
        days=days,
        completed=completed,
        in_progress=in_progress,
        upcoming=upcoming,
        total=total,
        total_done=total_done,
        total_pending=total - total_done,
        period_start=period_start.strftime("%d/%m/%Y"),
        period_end=today.strftime("%d/%m/%Y"),
    )


def get_burndown_data(tasks_by_date: dict, sprint_days: int = 14) -> BurndownData:
    """Compute burndown chart data."""
    today = datetime.now().date()
    start_date = today - timedelta(days=sprint_days - 1)

    all_tasks = []
    for tasks in tasks_by_date.values():
        all_tasks.extend(tasks)

    total_tasks = len(all_tasks)
    if total_tasks == 0:
        return BurndownData(
            sprint_days=sprint_days, total_tasks=0, current_remaining=0,
            ideal_per_day=0, velocity=0, points=[],
        )

    points: List[BurndownPoint] = []
    for day_offset in range(sprint_days):
        current_date = start_date + timedelta(days=day_offset)
        done_by_date = 0
        for task in all_tasks:
            if task.is_finished():
                task_date = task.date.date() if task.date else today
                if task_date <= current_date:
                    done_by_date += 1
        remaining = total_tasks - done_by_date
        points.append(BurndownPoint(
            date=current_date.strftime("%d/%m"),
            remaining=remaining,
        ))

    current_remaining = points[-1].remaining if points else total_tasks
    ideal_per_day = total_tasks / max(sprint_days - 1, 1)

    return BurndownData(
        sprint_days=sprint_days,
        total_tasks=total_tasks,
        current_remaining=current_remaining,
        ideal_per_day=ideal_per_day,
        velocity=total_tasks - current_remaining,
        points=points,
    )


def get_blockers_data(tasks_by_date: dict) -> List[BlockerInfo]:
    """Get all tasks that have blocker/blocks relationships."""
    from .tm_features import is_task_blocked

    results: List[BlockerInfo] = []

    for tasks in tasks_by_date.values():
        for task in tasks:
            if task.is_finished():
                continue
            blocked_by = task.blocked_by or []
            blocks = task.blocks or []
            if blocked_by or blocks:
                results.append(BlockerInfo(
                    task=_task_to_view_item(task),
                    blocked_by=list(blocked_by),
                    blocks=list(blocks),
                    is_blocked=is_task_blocked(task, tasks_by_date),
                ))

    return results


def get_time_tracking_data(tasks_by_date: dict) -> List[TimeTrackingItem]:
    """Get all tasks with time tracking data."""
    from .tm_features import format_time_spent

    results: List[TimeTrackingItem] = []

    for tasks in tasks_by_date.values():
        for task in tasks:
            minutes = task.time_spent
            if minutes and minutes > 0:
                results.append(TimeTrackingItem(
                    task=_task_to_view_item(task),
                    minutes_spent=minutes,
                    formatted=format_time_spent(minutes),
                ))

    # Sort by time spent descending
    results.sort(key=lambda x: x.minutes_spent, reverse=True)
    return results


def get_all_tags_data(tasks_by_date: dict) -> Dict[str, int]:
    """Get all tags with their counts."""
    from .tm_features import get_all_tags
    return get_all_tags(tasks_by_date)
