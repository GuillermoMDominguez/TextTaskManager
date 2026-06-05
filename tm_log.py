"""System log module — fixed single-line bottom bar.

Uses ANSI scroll regions to pin a status line at the very bottom of the
terminal. All other content (tasks, prompt) lives in the scroll region above.

Usage:
    from tm_log import log, render_log, set_visible, setup_scroll_region

    setup_scroll_region()  # call once on startup / after clear_screen
    log("sync", "Pushed successfully")
    render_log()           # draws the bottom bar without moving content
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
    """Set terminal scroll region to exclude the bottom 2 lines (divider + log).

    Call this after every clear_screen(). Only activates if visible AND there's
    a message to show.
    """
    if not _visible or not _message:
        return
    rows, _ = shutil.get_terminal_size()
    # Scroll region: line 1 to (rows - 2), leaving 2 lines at bottom
    sys.stdout.write(f"\033[1;{rows - 2}r")
    # Move cursor to top of scroll region
    sys.stdout.write("\033[1;1H")
    sys.stdout.flush()


def render_log() -> None:
    """Draw the bottom bar at the fixed bottom of the terminal."""
    if not _visible or not _message:
        return

    from tm_ui import Colors

    rows, cols = shutil.get_terminal_size()

    # Ensure scroll region is active (excludes bottom 2 lines)
    sys.stdout.write(f"\033[1;{rows - 2}r")

    # Save cursor position
    sys.stdout.write("\033[s")

    # Move to the second-to-last row (divider line)
    divider_row = rows - 1
    bar_row = rows

    # Draw subtle divider
    sys.stdout.write(f"\033[{divider_row};1H")
    divider = "┄" * cols
    sys.stdout.write(f"{Colors.DIM}{divider}{Colors.RESET}")

    # Draw log message on last row
    sys.stdout.write(f"\033[{bar_row};1H")
    ts = time.strftime("%H:%M:%S", time.localtime(_timestamp))
    icon = _category_icon(_category)
    color = _category_color(_category)
    content = f" {ts} {color}{icon} {_message}{Colors.RESET}"
    # Pad/truncate to terminal width
    # Strip ANSI for length calc
    visible_len = len(f" {ts} {icon} {_message}")
    padding = " " * max(0, cols - visible_len)
    sys.stdout.write(f"{Colors.DIM}{content}{padding}")

    # Restore cursor position (back in scroll region)
    sys.stdout.write(f"\033[u")
    sys.stdout.flush()


def clear_log_bar() -> None:
    """Erase the bottom bar area (e.g. before resetting scroll region)."""
    rows, cols = shutil.get_terminal_size()
    sys.stdout.write("\033[s")
    sys.stdout.write(f"\033[{rows - 1};1H" + " " * cols)
    sys.stdout.write(f"\033[{rows};1H" + " " * cols)
    sys.stdout.write("\033[u")
    sys.stdout.flush()


def reset_scroll_region() -> None:
    """Reset scroll region to full terminal (call on exit)."""
    rows, _ = shutil.get_terminal_size()
    sys.stdout.write(f"\033[1;{rows}r")
    sys.stdout.flush()


def set_visible(visible: bool) -> None:
    """Toggle log bar visibility."""
    global _visible
    _visible = visible
    if not visible:
        clear_log_bar()
        reset_scroll_region()


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
