"""System log module — single-line bottom bar.

Draws a status bar at the last 2 rows of the terminal using save/restore
cursor positioning. NO scroll regions — content and prompt flow naturally.

Usage:
    from tm_log import log, render_log, set_visible

    log("sync", "Pushed successfully")
    render_log()  # draws at absolute bottom without moving visible cursor
"""

import sys
import time
import shutil


_message: str = ""
_category: str = ""
_timestamp: float = 0.0
_visible: bool = True


# ─── Public API ────────────────────────────────────────────────────────────────

def log(category: str, message: str) -> None:
    """Set the current log bar message (replaces previous)."""
    global _message, _category, _timestamp
    _message = message
    _category = category
    _timestamp = time.time()


def setup_scroll_region() -> None:
    """No-op — kept for backward compatibility. Scroll regions removed."""
    pass


def render_log() -> None:
    """Draw the log bar as a single line at the absolute last row of the terminal.

    Uses save/restore cursor so the visible cursor position is unchanged.
    Safe to call from any context (main thread or background thread).
    """
    if not _visible or not _message:
        return

    from tm_ui import Colors

    rows, cols = shutil.get_terminal_size()

    # Save cursor, draw at bottom row, restore cursor
    sys.stdout.write("\033[s")  # save cursor

    # Single line at row `rows`: ┄ HH:MM:SS [category] message
    sys.stdout.write(f"\033[{rows};1H\033[2K")
    ts = time.strftime("%H:%M:%S", time.localtime(_timestamp))
    icon = _category_icon(_category)
    color = _category_color(_category)
    content = f"┄ {ts} {color}{icon} {_message}{Colors.RESET}"
    visible_len = len(f"┄ {ts} {icon} {_message}")
    padding = " " * max(0, cols - visible_len)
    sys.stdout.write(f"{Colors.DIM}{content}{padding}")

    # Restore cursor
    sys.stdout.write("\033[u")
    sys.stdout.flush()


def clear_log_bar() -> None:
    """Erase the log bar (last row)."""
    rows, cols = shutil.get_terminal_size()
    sys.stdout.write("\033[s")
    sys.stdout.write(f"\033[{rows};1H" + " " * cols)
    sys.stdout.write("\033[u")
    sys.stdout.flush()


def reset_scroll_region() -> None:
    """No-op — kept for backward compatibility. Scroll regions removed."""
    pass


def set_visible(visible: bool) -> None:
    """Toggle log bar visibility."""
    global _visible
    _visible = visible
    if not visible:
        clear_log_bar()


def is_visible() -> bool:
    """Check if log bar is currently visible."""
    return _visible


def clear() -> None:
    """Clear the current log message and erase the bar."""
    global _message, _category, _timestamp
    _message = ""
    _category = ""
    _timestamp = 0.0
    clear_log_bar()


def get_message() -> str:
    """Get the current log message."""
    return _message


# ─── Private helpers ───────────────────────────────────────────────────────────

def _category_icon(category: str) -> str:
    icons = {
        "sync": "[sync]",
        "error": "[err]",
        "warn": "[warn]",
        "info": "[info]",
    }
    return icons.get(category, "[log]")


def _category_color(category: str) -> str:
    from tm_ui import Colors
    colors = {
        "sync": Colors.DIM,
        "error": Colors.ERROR,
        "warn": "\033[33m",
        "info": Colors.DIM,
    }
    return colors.get(category, Colors.DIM)
