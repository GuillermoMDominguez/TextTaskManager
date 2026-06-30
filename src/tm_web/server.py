"""HTTP server and REST API for TextTaskManager web UI.

Zero external dependencies — pure stdlib.

Architecture:
    server.py       — HTTP server bootstrap, routing dispatcher, startup
    state.py        — WebState + shared _state global
    serializers.py  — JSON response helpers + task serialization
    routes.py       — API_ROUTES table
    handlers/       — One module per domain (tasks, views, notes, system, jira)
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .routes import API_ROUTES
from .serializers import error_response
from .state import WebState, set_web_state

_STATIC_DIR = Path(__file__).parent / "static"

# Re-export _PROJECT_ROOT for handler modules that need it
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ─── HTTP Handler ─────────────────────────────────────────────────────────────

class TTMRequestHandler(SimpleHTTPRequestHandler):
    """Custom handler: serves static files + JSON API."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_STATIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        route_key = ("GET", parsed.path)
        if route_key in API_ROUTES:
            API_ROUTES[route_key](self, params)
            return

        if parsed.path == "/" or parsed.path == "":
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path.startswith("/api/tasks/"):
            parts = parsed.path.split("/")
            if len(parts) == 5:
                task_id = parts[3]
                action = parts[4]
                params["task_id"] = [task_id]
                route_key = ("POST", f"/api/tasks/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

            if len(parts) == 6:
                task_id = parts[3]
                sub = parts[4]
                action = parts[5]
                params["task_id"] = [task_id]
                route_key = ("POST", f"/api/tasks/{sub}/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

        if parsed.path.startswith("/api/subtasks/"):
            parts = parsed.path.split("/")
            if len(parts) == 5:
                subtask_id = parts[3]
                action = parts[4]
                params["subtask_id"] = [subtask_id]
                route_key = ("POST", f"/api/subtasks/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return
            elif len(parts) == 6:
                subtask_id = parts[3]
                action = f"{parts[4]}/{parts[5]}"
                params["subtask_id"] = [subtask_id]
                route_key = ("POST", f"/api/subtasks/{action}")
                if route_key in API_ROUTES:
                    API_ROUTES[route_key](self, params)
                    return

        route_key = ("POST", parsed.path)
        if route_key in API_ROUTES:
            API_ROUTES[route_key](self, params)
            return

        error_response(self, "Not found", 404)

    def do_DELETE(self):
        self.do_POST()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


# ─── Server Entry Point ───────────────────────────────────────────────────────

_server_instance: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_server_port: int = 8080


def get_url() -> str:
    return f"http://127.0.0.1:{_server_port}"


def _bind_available_server(start_port: int, attempts: int = 50) -> Optional[tuple[HTTPServer, int]]:
    for port in range(start_port, start_port + attempts):
        try:
            return HTTPServer(("127.0.0.1", port), TTMRequestHandler), port
        except OSError:
            continue
    return None


def is_running() -> bool:
    return _server_thread is not None and _server_thread.is_alive()


def _open_browser_app_mode(url: str) -> bool:
    if sys.platform == "darwin":
        browsers = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif sys.platform == "win32":
        browsers = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
    else:
        browsers = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("brave-browser"),
            shutil.which("brave"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            shutil.which("microsoft-edge"),
        ]
        browsers = [b for b in browsers if b]

    for browser_path in browsers:
        if browser_path and os.path.exists(browser_path):
            try:
                subprocess.Popen(
                    [browser_path, f"--app={url}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                continue

    webbrowser.open(url)
    return False


def _wait_for_server(port: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)


def start_server_background(journal_path: str, script_dir: Path, port: int = 8080, open_browser: bool = True) -> bool:
    """Start the web UI server in a background daemon thread."""
    global _server_instance, _server_thread, _server_port

    if is_running():
        return True

    bound = _bind_available_server(port)
    if bound is None:
        return False

    _server_instance, _server_port = bound
    set_web_state(WebState(journal_path, script_dir))

    # Initialize git sync (same logic as task_manager.py)
    from src.tm_settings import load_settings
    from src.tm_sync import init_sync, sync_push_async, sync_pull
    from src.tm_sync import shutdown as sync_shutdown
    from src.tm_journal import register_post_write_hook
    import atexit

    settings = load_settings(script_dir)
    journals_dir = Path(journal_path).parent
    sync_active = init_sync(journals_dir, settings, script_dir)
    if sync_active:
        register_post_write_hook(sync_push_async)
        atexit.register(sync_shutdown)
        sync_pull(interactive=False)

    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    _server_thread.start()

    if open_browser:
        _wait_for_server(_server_port)
        _open_browser_app_mode(get_url())

    return True


def stop_server() -> None:
    global _server_instance, _server_thread

    if _server_instance is not None:
        _server_instance.shutdown()
        _server_instance.server_close()
        _server_instance = None
    _server_thread = None


def start_server(journal_path: str, script_dir: Path, port: int = 8080, open_browser: bool = True) -> None:
    """Start the web UI server (blocking, for standalone mode)."""
    global _server_port
    bound = _bind_available_server(port)
    if bound is None:
        raise OSError(f"No available port found from {port} to {port + 49}")

    server, _server_port = bound
    set_web_state(WebState(journal_path, script_dir))

    # Initialize git sync (same logic as task_manager.py)
    from src.tm_settings import load_settings
    from src.tm_sync import init_sync, sync_push_async, sync_pull
    from src.tm_sync import shutdown as sync_shutdown
    from src.tm_journal import register_post_write_hook
    import atexit

    settings = load_settings(script_dir)
    journals_dir = Path(journal_path).parent
    sync_active = init_sync(journals_dir, settings, script_dir)
    if sync_active:
        register_post_write_hook(sync_push_async)
        atexit.register(sync_shutdown)
        sync_pull(interactive=False)

    url = get_url()

    print(f"\n  Web UI running at: \033[1m\033[96m{url}\033[0m")
    print(f"  Journal: {journal_path}")
    print(f"  Press Ctrl+C to stop\n")

    if open_browser:
        _open_browser_app_mode(url)

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
    script_dir = Path(args.journal).resolve().parent.parent
    start_server(args.journal, script_dir, args.port, not args.no_browser)
