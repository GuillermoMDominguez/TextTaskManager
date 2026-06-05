"""System log — stores status messages and history, printed above the prompt.

Usage:
    from .tm_log import log, get_status_line, get_history, set_visible

    log("sync", "Pushed successfully")
    # get_status_line() returns the last formatted line, or ""
    # get_history() returns all stored entries formatted
"""

import sys
import time
from collections import deque

MAX_HISTORY = 50

_message: str = ""
_category: str = ""
_timestamp: float = 0.0
_visible: bool = True
_history: deque = deque(maxlen=MAX_HISTORY)


# ─── Public API ────────────────────────────────────────────────────────────────

def log(category: str, message: str) -> None:
    """Store a status message and append to history."""
    global _message, _category, _timestamp
    _message = message
    _category = category
    _timestamp = time.time()
    _history.append((_timestamp, category, message))
    _update_title(message)


def get_status_line() -> str:
    """Return the formatted status line, or empty string if nothing to show."""
    if not _visible or not _message:
        return ""
    from .tm_ui import Colors
    ts = time.strftime("%H:%M:%S", time.localtime(_timestamp))
    color = _category_color(_category)
    icon = _category_icon(_category)
    return f"{Colors.DIM}┄ {ts} {color}{icon} {_message}{Colors.RESET}"


def get_history() -> list[str]:
    """Return all history entries as formatted strings."""
    from .tm_ui import Colors
    lines = []
    for ts, cat, msg in _history:
        t = time.strftime("%H:%M:%S", time.localtime(ts))
        color = _category_color(cat)
        icon = _category_icon(cat)
        lines.append(f"{Colors.DIM}{t} {color}{icon} {msg}{Colors.RESET}")
    return lines


def set_visible(visible: bool) -> None:
    """Toggle log visibility."""
    global _visible
    _visible = visible


def is_visible() -> bool:
    return _visible


def clear() -> None:
    """Clear the current message and all history."""
    global _message, _category, _timestamp
    _message = ""
    _category = ""
    _timestamp = 0.0
    _history.clear()
    _update_title("")


def get_message() -> str:
    """Get the raw current message text."""
    return _message


# ─── Private helpers ───────────────────────────────────────────────────────────

def _update_title(message: str) -> None:
    """Set the terminal tab/window title."""
    title = f"TTM | {message}" if message else "TTM"
    sys.stdout.write(f"\033]0;{title}\007")
    sys.stdout.flush()


def _category_icon(category: str) -> str:
    icons = {
        "sync": "[sync]",
        "error": "[err]",
        "warn": "[warn]",
        "info": "[info]",
    }
    return icons.get(category, "[log]")


def _category_color(category: str) -> str:
    from .tm_ui import Colors
    colors = {
        "sync": Colors.DIM,
        "error": Colors.ERROR,
        "warn": "\033[33m",
        "info": Colors.DIM,
    }
    return colors.get(category, Colors.DIM)
