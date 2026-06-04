"""Interactive ANSI terminal form widget.

Provides a navigable form with fields (text, select) that works
cross-platform (Windows, Linux, macOS) without external dependencies.

Navigation:
  Tab / Down      -> Next field
  Shift+Tab / Up  -> Previous field
  Enter           -> Accept form (when on [Accept] button)
  Esc             -> Cancel form
  Left/Right      -> Cycle options in select fields / move cursor in text
"""

import sys
import os
from typing import List, Optional, Dict, Any


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

    def render(self, active: bool, box_bg: str, width: int = 40) -> str:
        if active:
            before = self.value[: self.cursor_pos]
            cursor_ch = self.value[self.cursor_pos] if self.cursor_pos < len(self.value) else " "
            after = self.value[self.cursor_pos + 1:] if self.cursor_pos < len(self.value) else ""
            return f"{before}\033[7m{cursor_ch}\033[27m{after}"
        elif self.value:
            return self.value
        else:
            return f"\033[2m{self.placeholder}\033[22m"

    def get_value(self) -> str:
        return self.value


class SelectField:
    """Cycle-through selection field."""

    def __init__(self, label: str, options: List[str], selected: int = 0, allow_empty: bool = False):
        self.label = label
        self._options = list(options)
        self.allow_empty = allow_empty
        if allow_empty:
            self._options = ["(none)"] + self._options
            self.selected = 0
        else:
            self.selected = selected

    def handle_key(self, key: str) -> None:
        if key in ("LEFT", "BACKSPACE"):
            self.selected = (self.selected - 1) % len(self._options)
        elif key in ("RIGHT",) or (len(key) == 1 and key == " "):
            self.selected = (self.selected + 1) % len(self._options)

    def render(self, active: bool, box_bg: str, width: int = 40) -> str:
        parts = []
        for i, opt in enumerate(self._options):
            if i == self.selected:
                if active:
                    parts.append(f"\033[7m\033[97m {opt} \033[27m\033[0m{box_bg}")
                else:
                    parts.append(f"\033[1m\033[96m[{opt}]\033[22m\033[0m{box_bg}")
            else:
                parts.append(f"\033[2m {opt} \033[22m")
        return "".join(parts)

    def get_value(self) -> str:
        val = self._options[self.selected]
        if self.allow_empty and val == "(none)":
            return ""
        return val


# ─── Form Renderer ─────────────────────────────────────────────────────────────

# Form box uses a slightly lighter background to stand out
_FORM_BG = "\033[48;2;25;25;35m"


def _hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


def _show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def _move_to(row: int, col: int):
    sys.stdout.write(f"\033[{row};{col}H")


def _term_bg() -> str:
    """Get the terminal background sequence."""
    try:
        from tm_ui import _BG_SEQ
        return _BG_SEQ
    except ImportError:
        return "\033[48;2;0;0;0m"


def show_form(
    title: str,
    fields: List[Any],
    start_row: int = 3,
) -> Optional[Dict[str, str]]:
    """Display an interactive form and return field values or None if cancelled.

    Returns:
        Dict mapping field labels to their string values, or None if cancelled.
    """
    bg = _FORM_BG
    term_bg = _term_bg()
    active_idx = 0
    button_count = 2
    total_items = len(fields) + button_count

    def _clear_line(row: int, left: int, width: int):
        _move_to(row, left)
        sys.stdout.write(f"{bg}{' ' * width}\033[0m")

    def _render():
        term_width = os.get_terminal_size().columns
        box_width = min(64, term_width - 4)
        left = max(2, (term_width - box_width) // 2)

        row = start_row
        # Top border
        _clear_line(row, left, box_width)
        _move_to(row, left)
        sys.stdout.write(f"{bg}\033[36m┌{'─' * (box_width - 2)}┐\033[0m")
        row += 1

        # Title
        _clear_line(row, left, box_width)
        _move_to(row, left)
        title_padded = f" {title} ".ljust(box_width - 2)
        sys.stdout.write(f"{bg}\033[36m│\033[0m{bg}\033[1m\033[97m{title_padded}\033[0m{bg}\033[36m│\033[0m")
        row += 1

        # Title separator
        _clear_line(row, left, box_width)
        _move_to(row, left)
        sys.stdout.write(f"{bg}\033[36m├{'─' * (box_width - 2)}┤\033[0m")
        row += 1

        # Fields
        for i, field in enumerate(fields):
            is_active = (i == active_idx)
            _clear_line(row, left, box_width)
            _move_to(row, left)

            label_str = f" {field.label}:"
            if is_active:
                label_col = "\033[1m\033[93m"  # Bold yellow
                indicator = "▸"
            else:
                label_col = "\033[37m"
                indicator = " "

            rendered = field.render(is_active, bg, box_width - 20)
            sys.stdout.write(
                f"{bg}\033[36m│\033[0m{bg}"
                f"{label_col}{indicator}{label_str.ljust(14)}\033[0m{bg}"
                f" {rendered}\033[0m{bg}"
                f"\033[{box_width}G\033[36m│\033[0m"
            )
            row += 1

        # Button separator
        _clear_line(row, left, box_width)
        _move_to(row, left)
        sys.stdout.write(f"{bg}\033[36m├{'─' * (box_width - 2)}┤\033[0m")
        row += 1

        # Buttons row
        _clear_line(row, left, box_width)
        _move_to(row, left)
        accept_active = (active_idx == len(fields))
        cancel_active = (active_idx == len(fields) + 1)

        if accept_active:
            accept_str = "\033[7m\033[92m  Accept  \033[0m" + bg
        else:
            accept_str = "\033[32m  Accept  \033[0m" + bg

        if cancel_active:
            cancel_str = "\033[7m\033[91m  Cancel  \033[0m" + bg
        else:
            cancel_str = "\033[2m  Cancel  \033[0m" + bg

        sys.stdout.write(
            f"{bg}\033[36m│\033[0m{bg}"
            f"    {accept_str}    {cancel_str}"
            f"\033[{box_width}G\033[36m│\033[0m"
        )
        row += 1

        # Bottom border
        _clear_line(row, left, box_width)
        _move_to(row, left)
        sys.stdout.write(f"{bg}\033[36m└{'─' * (box_width - 2)}┘\033[0m")
        row += 1

        # Help text
        _move_to(row, left)
        sys.stdout.write(f"{term_bg}\033[2m  Tab/↑↓: navigate │ Enter: accept │ Esc: cancel │ ←→: options\033[0m{term_bg}")

        sys.stdout.flush()

    total_form_rows = len(fields) + 7  # borders + title + separator + buttons + help

    _hide_cursor()
    try:
        while True:
            _render()
            key = _read_key()

            if key == "ESC":
                return None
            elif key in ("TAB", "DOWN"):
                active_idx = (active_idx + 1) % total_items
            elif key in ("SHIFT_TAB", "UP"):
                active_idx = (active_idx - 1) % total_items
            elif key == "ENTER":
                if active_idx == len(fields):  # Accept
                    break
                elif active_idx == len(fields) + 1:  # Cancel
                    return None
                else:
                    active_idx = (active_idx + 1) % total_items
            else:
                if active_idx < len(fields):
                    fields[active_idx].handle_key(key)
    finally:
        _show_cursor()
        # Clear the form area
        term_width = os.get_terminal_size().columns
        for r in range(start_row, start_row + total_form_rows + 1):
            _move_to(r, 1)
            sys.stdout.write(f"{term_bg}{' ' * term_width}")
        _move_to(start_row, 1)
        sys.stdout.flush()

    # Build result
    result = {}
    for field in fields:
        result[field.label] = field.get_value()
    return result
