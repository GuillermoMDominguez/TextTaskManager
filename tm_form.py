"""Interactive ANSI terminal form widget.

Cross-platform (Windows, Linux, macOS), no external dependencies.

Navigation:
  Tab / Down      -> Next field
  Shift+Tab / Up  -> Previous field
  Enter           -> Accept (on button) or next field
  Esc             -> Cancel
  Left/Right      -> Cycle select options / move cursor in text
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
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            mapping = {
                "H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                "S": "DELETE",
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
                            sys.stdin.read(1)
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

    def render(self, active: bool) -> str:
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
            # selected=-1 means no selection → start at "(none)"
            self.selected = selected + 1 if selected >= 0 else 0
        else:
            self.selected = max(0, min(selected, len(self._options) - 1))

    def handle_key(self, key: str) -> None:
        if key in ("LEFT", "BACKSPACE"):
            self.selected = (self.selected - 1) % len(self._options)
        elif key in ("RIGHT",) or (len(key) == 1 and key == " "):
            self.selected = (self.selected + 1) % len(self._options)

    def render(self, active: bool) -> str:
        parts = []
        for i, opt in enumerate(self._options):
            if i == self.selected:
                if active:
                    parts.append(f"\033[7m {opt} \033[27m")
                else:
                    parts.append(f"\033[1m[{opt}]\033[22m")
            else:
                parts.append(f"\033[2m {opt} \033[22m")
        return "".join(parts)

    def get_value(self) -> str:
        val = self._options[self.selected]
        if self.allow_empty and val == "(none)":
            return ""
        return val


# ─── Form Display ──────────────────────────────────────────────────────────────

# Dark grey-blue box background to stand out from terminal black
_FORM_BG = "\033[48;2;28;28;38m"
_BORDER_COLOR = "\033[36m"
_RST = "\033[0m"


def _write(s: str):
    sys.stdout.write(s)


def _flush():
    sys.stdout.flush()


def show_form(
    title: str,
    fields: List[Any],
    start_row: int = 3,
) -> Optional[Dict[str, str]]:
    """Display an interactive form. Returns dict of values or None if cancelled."""
    active_idx = 0
    total_items = len(fields) + 2  # fields + Accept + Cancel

    def _term_width() -> int:
        try:
            return os.get_terminal_size().columns
        except (ValueError, OSError):
            return 80

    def _draw():
        tw = _term_width()
        box_w = min(62, tw - 4)
        pad = " " * box_w

        row = start_row
        # Top
        _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} ┌{'─' * (box_w - 2)}┐ {_RST}")
        row += 1
        # Title
        t = f" {title}"
        t_padded = t + " " * (box_w - 2 - len(t))
        _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} │{_RST}{_FORM_BG}\033[1m\033[97m{t_padded}{_RST}{_FORM_BG}{_BORDER_COLOR}│ {_RST}")
        row += 1
        # Sep
        _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} ├{'─' * (box_w - 2)}┤ {_RST}")
        row += 1

        # Fields
        for i, field in enumerate(fields):
            is_active = (i == active_idx)
            indicator = "\033[93m▸" if is_active else " "
            lbl = field.label + ":"
            if is_active:
                lbl_str = f"\033[1m\033[93m{lbl.ljust(13)}{_RST}{_FORM_BG}"
            else:
                lbl_str = f"\033[37m{lbl.ljust(13)}{_RST}{_FORM_BG}"

            rendered = field.render(is_active)
            # Build line content (without worrying about exact width)
            content = f"{indicator} {lbl_str} {rendered}"
            _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} │{_RST}{_FORM_BG}{content}{_RST}{_FORM_BG}\033[K{_BORDER_COLOR}│ {_RST}")
            # Erase to end and place right border at fixed column
            # Use erase-to-right then overwrite at column
            _write(f"\033[{row};{box_w + 1}H{_FORM_BG}{_BORDER_COLOR}│ {_RST}")
            row += 1

        # Sep
        _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} ├{'─' * (box_w - 2)}┤ {_RST}")
        row += 1

        # Buttons
        acc_idx = len(fields)
        can_idx = len(fields) + 1
        if active_idx == acc_idx:
            acc = "\033[7m\033[92m  Accept  \033[27m\033[0m"
        else:
            acc = "\033[32m  Accept  \033[0m"
        if active_idx == can_idx:
            can = "\033[7m\033[91m  Cancel  \033[27m\033[0m"
        else:
            can = "\033[2m  Cancel  \033[0m"

        _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} │{_RST}{_FORM_BG}   {acc}{_FORM_BG}   {can}{_FORM_BG}\033[K")
        _write(f"\033[{row};{box_w + 1}H{_FORM_BG}{_BORDER_COLOR}│ {_RST}")
        row += 1

        # Bottom
        _write(f"\033[{row};1H{_FORM_BG}{_BORDER_COLOR} └{'─' * (box_w - 2)}┘ {_RST}")
        row += 1

        # Help
        _write(f"\033[{row};1H\033[2m  Tab/↑↓: navigate  Enter: accept  Esc: cancel  ←→: options\033[0m\033[K")

        _flush()

    total_rows = len(fields) + 8

    # Hide cursor
    _write("\033[?25l")
    _flush()

    cancelled = True
    try:
        while True:
            _draw()
            key = _read_key()

            if key == "ESC":
                break
            elif key in ("TAB", "DOWN"):
                active_idx = (active_idx + 1) % total_items
            elif key in ("SHIFT_TAB", "UP"):
                active_idx = (active_idx - 1) % total_items
            elif key == "ENTER":
                if active_idx == len(fields):  # Accept
                    cancelled = False
                    break
                elif active_idx == len(fields) + 1:  # Cancel
                    break
                else:
                    active_idx = (active_idx + 1) % total_items
            elif key:
                if active_idx < len(fields):
                    fields[active_idx].handle_key(key)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        # Show cursor
        _write("\033[?25h")
        # Clear form area
        try:
            tw = _term_width()
            term_bg = "\033[0m"
            try:
                from tm_ui import _BG_SEQ
                term_bg = _BG_SEQ
            except ImportError:
                pass
            for r in range(start_row, start_row + total_rows + 1):
                _write(f"\033[{r};1H{term_bg}\033[K")
            _write(f"\033[{start_row};1H")
        except Exception:
            pass
        _flush()

    if cancelled:
        return None

    result = {}
    for field in fields:
        result[field.label] = field.get_value()
    return result
