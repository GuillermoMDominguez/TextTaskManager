"""HTTP response helpers and JSON serializers for the web API."""

import json
from datetime import datetime
from typing import Any


def json_response(handler, data: Any, status: int = 200) -> None:
    """Send a JSON response."""
    body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def error_response(handler, message: str, status: int = 400) -> None:
    """Send an error JSON response."""
    json_response(handler, {"error": message}, status)


def read_body(handler) -> dict:
    """Read and parse JSON body from request."""
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def serialize_task(item) -> dict:
    """Serialize a TaskViewItem to dict."""
    return {
        "id": item.task_id,
        "title": item.title,
        "state": item.state,
        "priority": item.priority,
        "date": item.date.strftime("%d/%m/%Y") if item.date else None,
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
                "linked_notes": st.linked_notes or [],
            }
            for st in item.subtasks
        ],
        "recurrence": item.recurrence,
        "time_spent": item.time_spent,
        "jira_key": item.jira_key,
        "linked_notes": item.linked_notes,
        "blocked_by": item.blocked_by[:],
        "blocks": item.blocks[:],
    }
