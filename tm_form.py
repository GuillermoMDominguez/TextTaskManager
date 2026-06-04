"""Interactive ANSI terminal form widget.

Provides a navigable form with fields (text, select) that works
cross-platform (Windows, Linux, macOS) without external dependencies.

Navigation:
  Tab / Down      → Next field
  Shift+Tab / Up  → Previous field
  Enter           → Accept form (when on last field or [Accept] button)
  Esc             → Cancel form
  Left/Right      → Cycle options in select fields
"""

import sys
import os
from typing import List, Optional, Dict, Any, Tuple


# ─── Cross-platform key reading ───────────────────────────────────────────────

if os.name == "nt":
    import msvcrt

    def _read_key() -> str:
        """Read a single keypress on Windows."""
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # Special key prefix
            ch2 = msvcrt.getwch()
            mapping = {
                "H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                "S": "DELETE", "\x0f": "SHIFT_TAB",
            }
            return mapping.get(ch2, "")
        if ch == "\x1b":
            return "ESC"
        if ch == "\r":
            return "ENTER"
        if ch == "\t":
            return "TAB"
        if ch == "\x08":
            return "BACKSPACE"
        return ch
else:
    import tty
    import termios
    import select as _select

    def _read_key() -> str:
        """Read a single keypress on Unix/macOS."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                # Check for escape sequence
                if _select.select([sys.stdin], [], [], 0.05)[0]:
                    seq = sys.stdin.read(1)
                    if seq == "[":
                        code = sys.stdin.read(1)
                        if code == "A":
                            return "UP"
                        elif code == "B":
                            return "DOWN"
                        elif code == "C":
                            return "RIGHT"
                        elif code == "D":
                            return "LEFT"
                        elif code == "Z":
                            return "SHIFT_TAB"
                        elif code == "3":
                            sys.stdin.read(1)  # consume ~
                            return "DELETE"
                return "ESC"
            if ch == "\r" or ch == "\n":
                return "ENTER"
            if ch == "\t":
                return "TAB"
            if ch == "\x7f" or ch == "\x08":
                return "BACKSPACE"
            if ord(ch) < 32:
                return ""
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# ─── Form Field Types ──────────────────────────────────────────────────────────

class TextField:
    """Editable text input field."""

    def __init__(self, label: str, value: str = "", placeholder: str = ""):
        self.label = label
        self.value = value
        self.placeholder = placeholder
        self.cursor_pos = len(value)

    def handle_key(self, key: str) -> None:
        if key == "BACKSPACE":
            if self.cursor_pos > 0:
                self.value = self.value[: self.cursor_pos - 1] + self.value[self.cursor_pos:]
                self.cursor_pos -= 1
        elif key == "DELETE":
            if self.cursor_pos < len(self.value):
                self.value = self.value[: self.cursor_pos] + self.value[self.cursor_pos + 1:]
        elif key == "LEFT":
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
        elif key == "RIGHT":
            if self.cursor_pos < len(self.value):
                self.cursor_pos += 1
        elif len(key) == 1 and key.isprintable():
            self.value = self.value[: self.cursor_pos] + key + self.value[self.cursor_pos:]
            self.cursor_pos += 1

    def render(self, active: bool, width: int = 40) -> str:
        display = self.value if self.value else f"\033[2m{self.placeholder}\033[22m"
        if active:
            # Show cursor position
            before = self.value[: self.cursor_pos]
            cursor_ch = self.value[self.cursor_pos] if self.cursor_pos < len(self.value) else " "
            after = self.value[self.cursor_pos + 1:] if self.cursor_pos < len(self.value) else ""
            display = f"{before}\033[7m{cursor_ch}\033[27m{after}"
        return display


class SelectField:
    """Cycle-through selection field."""

    def __init__(self, label: str, options: List[str], selected: int = 0, allow_empty: bool = False):
        self.label = label
        self.options = options if not allow_empty else ["(none)"] + options
        self.selected = selected
        self.allow_empty = allow_empty

    def handle_key(self, key: str) -> None:
        if key in ("LEFT", "BACKSPACE"):
            self.selected = (self.selected - 1) % len(self.options)
        elif key in ("RIGHT",) or (len(key) == 1 and key == " "):
            self.selected = (self.selected + 1) % len(self.options)

    def render(self, active: bool, width: int = 40) -> str:
        parts = []
        for i, opt in enumerate(self.options):
            if i == self.selected:
                if active:
                    parts.append(f"\033[7m {opt} \033[27m")
                else:
                    parts.append(f"\033[1m[{opt}]\033[22m")
            else:
                parts.append(f" {opt} ")
        return "".join(parts)

    @property
    def value(self) -> str:
        val = self.options[self.selected]
        if self.allow_empty and val == "(none)":
            return ""
        return val


# ─── Form Renderer ─────────────────────────────────────────────────────────────

_BG = "\033[48;2;0;0;0m"


def _get_bg() -> str:
    """Get background sequence (imports from tm_ui if available)."""
    try:
        from tm_ui import _BG_SEQ
        return _BG_SEQ
    except ImportError:
        return _BG


def _hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


def _show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def _move_to(row: int, col: int):
    sys.stdout.write(f"\033[{row};{col}H")


def show_form(
    title: str,
    fields: List[Any],
    start_row: int = 3,
) -> Optional[Dict[str, str]]:
    """Display an interactive form and return field values or None if cancelled.

    Returns:
        Dict mapping field labels to their values, or None if user pressed Esc.
    """
    bg = _get_bg()
    active_idx = 0
    # Number of button "fields": Accept / Cancel
    button_count = 2
    total_items = len(fields) + button_count
    button_labels = ["  [Accept]  ", "  [Cancel]  "]

    def _render():
        """Render the full form."""
        term_width = os.get_terminal_size().columns
        box_width = min(60, term_width - 4)
        left_margin = max(2, (term_width - box_width) // 2)

        _move_to(start_row, left_margin)
        # Title bar
        sys.stdout.write(f"{bg}\033[1m\033[96m{'─' * box_width}\033[0m{bg}")
        _move_to(start_row + 1, left_margin)
        sys.stdout.write(f"{bg}\033[1m\033[97m  {title}{' ' * (box_width - len(title) - 2)}\033[0m{bg}")
        _move_to(start_row + 2, left_margin)
        sys.stdout.write(f"{bg}\033[96m{'─' * box_width}\033[0m{bg}")

        row = start_row + 3
        for i, field in enumerate(fields):
            is_active = (i == active_idx)
            _move_to(row, left_margin)
            label_str = f"  {field.label}:"
            # Highlight active field label
            if is_active:
                sys.stdout.write(f"{bg}\033[1m\033[93m{label_str.ljust(16)}\033[0m{bg}")
            else:
                sys.stdout.write(f"{bg}\033[97m{label_str.ljust(16)}\033[0m{bg}")

            # Field value
            rendered = field.render(is_active, box_width - 18)
            sys.stdout.write(f"{bg}{rendered}\033[0m{bg}")
            # Clear rest of line
            sys.stdout.write(" " * max(0, box_width - 16 - len(field.value if hasattr(field, 'value') and isinstance(field.value, str) else "")))
            sys.stdout.write(f"\033[0m{bg}")
            row += 1

        # Separator
        row += 1
        _move_to(row, left_margin)
        sys.stdout.write(f"{bg}\033[96m{'─' * box_width}\033[0m{bg}")
        row += 1

        # Buttons
        _move_to(row, left_margin)
        for bi, blabel in enumerate(button_labels):
            btn_idx = len(fields) + bi
            is_btn_active = (active_idx == btn_idx)
            if is_btn_active:
                if bi == 0:  # Accept
                    sys.stdout.write(f"{bg}\033[7m\033[92m{blabel}\033[0m{bg}  ")
                else:  # Cancel
                    sys.stdout.write(f"{bg}\033[7m\033[91m{blabel}\033[0m{bg}  ")
            else:
                if bi == 0:
                    sys.stdout.write(f"{bg}\033[32m{blabel}\033[0m{bg}  ")
                else:
                    sys.stdout.write(f"{bg}\033[91m{blabel}\033[0m{bg}  ")

        row += 1
        _move_to(row, left_margin)
        sys.stdout.write(f"{bg}\033[96m{'─' * box_width}\033[0m{bg}")
        row += 1
        _move_to(row, left_margin)
        sys.stdout.write(f"{bg}\033[2m  Tab/↑↓: navigate  Enter: accept  Esc: cancel  ←→: options\033[0m{bg}")

        sys.stdout.flush()

    _hide_cursor()
    try:
        while True:
            _render()
            key = _read_key()

            if key == "ESC":
                return None

            if key in ("TAB", "DOWN"):
                active_idx = (active_idx + 1) % total_items
            elif key in ("SHIFT_TAB", "UP"):
                active_idx = (active_idx - 1) % total_items
            elif key == "ENTER":
                if active_idx == len(fields):  # Accept button
                    break
                elif active_idx == len(fields) + 1:  # Cancel button
                    return None
                else:
                    # On a field: move to next
                    active_idx = (active_idx + 1) % total_items
            else:
                # Pass key to active field
                if active_idx < len(fields):
                    fields[active_idx].handle_key(key)
    finally:
        _show_cursor()
        # Clear form area
        term_width = os.get_terminal_size().columns
        total_rows = len(fields) + 8
        for r in range(start_row, start_row + total_rows + 1):
            _move_to(r, 1)
            sys.stdout.write(f"{bg}{' ' * term_width}")
        _move_to(start_row, 1)
        sys.stdout.flush()

    # Build result
    result = {}
    for field in fields:
        result[field.label] = field.value
    return result
