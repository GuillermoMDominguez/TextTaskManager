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
    created: List[TaskViewItem]


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
