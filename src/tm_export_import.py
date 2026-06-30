"""Export/Import — JSON, CSV, Markdown export and JSON import."""

import csv
import io
import json
from collections import OrderedDict
from datetime import datetime
from typing import List

from .tm_config import DEFAULT_STATE


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
            state = item.get("state", DEFAULT_STATE)
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
                st_state = st.get("state", DEFAULT_STATE)
                lines.append(f"+ {st_title} -- {st_state}\n")

    return lines
