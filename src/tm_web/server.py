"""HTTP server and REST API for TextTaskManager web UI.

Zero external dependencies — pure stdlib.
"""

import json
import os
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

from src.tm_journal import parse_journal, add_task_to_file, update_task_state_in_file, write_journal
from src.tm_logic import assign_task_ids, get_id_width, normalize_state_input, find_task_by_id
from src.tm_models import Task
from src.tm_views_data import (
    get_agenda_data,
    get_kanban_data,
    get_stats_data,
    get_all_tasks_flat,
    get_pending_tasks,
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
            }
            for st in item.subtasks
        ],
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
                pass

        add_task_to_file(
            _state.journal_path,
            title=title,
            state=state,
            due_date=date_obj,
            priority=priority,
        )
        _state.refresh()
        _json_response(handler, {"ok": True}, 201)
    except Exception as e:
        _error_response(handler, str(e), 500)


# ─── Route Table ──────────────────────────────────────────────────────────────

API_ROUTES = {
    ("GET", "/api/tasks"): api_get_tasks,
    ("GET", "/api/agenda"): api_get_agenda,
    ("GET", "/api/kanban"): api_get_kanban,
    ("GET", "/api/stats"): api_get_stats,
    ("POST", "/api/tasks"): api_create_task,
    ("POST", "/api/tasks/state"): api_change_state,
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

        # Extract task_id from path like /api/tasks/3/state
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/state"):
            parts = parsed.path.split("/")
            # /api/tasks/<id>/state
            if len(parts) == 5:
                params["task_id"] = [parts[3]]
                route_key = ("POST", "/api/tasks/state")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

        route_key = ("POST", parsed.path)
        if route_key in API_ROUTES:
            API_ROUTES[route_key](self, params)
            return

        _error_response(self, "Not found", 404)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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


def is_running() -> bool:
    """Check if the background web server is running."""
    return _server_thread is not None and _server_thread.is_alive()


def start_server_background(journal_path: str, port: int = 8080, open_browser: bool = True) -> None:
    """Start the web UI server in a background daemon thread.

    The server runs until stop_server() is called or the process exits.
    """
    global _state, _server_instance, _server_thread, _server_port

    if is_running():
        return

    _server_port = port
    _state = WebState(journal_path)
    _server_instance = HTTPServer(("127.0.0.1", port), TTMRequestHandler)
    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    _server_thread.start()

    if open_browser:
        webbrowser.open(get_url())


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
        webbrowser.open(url)

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
