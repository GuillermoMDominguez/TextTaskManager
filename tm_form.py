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

    def _read_key() -> str:
        """Read a single keypress on Unix/macOS."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = os.read(fd, 1)
            if ch == b"\x1b":
                # Read rest of escape sequence using os.read with timeout
                import fcntl
                # Set non-blocking
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                try:
                    import time
                    time.sleep(0.02)  # Brief wait for sequence bytes
                    try:
                        seq = os.read(fd, 10)
                    except (OSError, BlockingIOError):
                        seq = b""
                finally:
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags)

                if seq == b"[A":
                    return "UP"
                elif seq == b"[B":
                    return "DOWN"
                elif seq == b"[C":
                    return "RIGHT"
                elif seq == b"[D":
                    return "LEFT"
                elif seq == b"[Z":
                    return "SHIFT_TAB"
                elif seq == b"[3~":
                    return "DELETE"
                elif seq == b"OA":
                    return "UP"
                elif seq == b"OB":
                    return "DOWN"
                elif seq == b"OC":
                    return "RIGHT"
                elif seq == b"OD":
                    return "LEFT"
                elif seq == b"":
                    return "ESC"
                return "ESC"
            if ch == b"\r" or ch == b"\n":
                return "ENTER"
            if ch == b"\t":
                return "TAB"
            if ch == b"\x7f" or ch == b"\x08":
                return "BACKSPACE"
            if len(ch) == 1 and ch[0] < 32:
                return ""
            return ch.decode("utf-8", errors="ignore")
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
            return f"\033[97m{before}\033[7m{cursor_ch}\033[27m{after}\033[0m"
        elif self.value:
            return f"\033[97m{self.value}\033[0m"
        else:
            return f"\033[2m\033[37m{self.placeholder}\033[0m"

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
        opt = self._options[self.selected]
        if active:
            left = "◂" if len(self._options) > 1 else " "
            right = "▸" if len(self._options) > 1 else " "
            return f"\033[2m{left}\033[22m \033[7m\033[97m {opt} \033[27m\033[22m \033[2m{right}\033[22m \033[2m[space]\033[22m"
        else:
            return f"\033[97m{opt}\033[22m"

    def get_value(self) -> str:
        val = self._options[self.selected]
        if self.allow_empty and val == "(none)":
            return ""
        return val


# ─── List Picker ───────────────────────────────────────────────────────────────


def show_list_picker(
    title: str,
    options: List[str],
    selected: int = 0,
) -> Optional[int]:
    """Full-screen vertical list picker. Returns selected index or None if cancelled.

    Shows ALL options at once with ▸ indicator on the selected one.
    Navigation: Up/Down to move, Enter to accept, Esc to cancel.
    Scrolls if options exceed terminal height.
    """

    def _term_size():
        try:
            sz = os.get_terminal_size()
            return sz.columns, sz.lines
        except (ValueError, OSError):
            return 80, 24

    cursor = max(0, min(selected, len(options) - 1))

    _PICK_BG = "\033[48;2;28;28;38m"
    _BD = "\033[36m"
    _R = "\033[0m"

    def _draw():
        tw, th = _term_size()
        # Box width: fit content or cap at terminal width - 4
        max_opt_len = max((len(o) for o in options), default=10)
        box_w = min(max_opt_len + 6, tw - 4)  # 6 = borders(2) + indicator(2) + padding(2)
        inner_w = box_w - 2

        # How many options fit (leave room for title, borders, help)
        max_visible = th - 6  # top + title + sep + bottom + help + 1 margin
        if max_visible < 3:
            max_visible = 3

        # Scroll window
        if len(options) <= max_visible:
            scroll_top = 0
            visible = options
            vis_range = range(len(options))
        else:
            # Keep cursor visible with some context
            half = max_visible // 2
            scroll_top = cursor - half
            if scroll_top < 0:
                scroll_top = 0
            if scroll_top + max_visible > len(options):
                scroll_top = len(options) - max_visible
            vis_range = range(scroll_top, scroll_top + max_visible)
            visible = [options[i] for i in vis_range]

        total_rows = len(visible) + 5  # top + title + sep + options + bottom
        col_off = max(1, (tw - box_w) // 2)
        start_row = max(1, (th - total_rows) // 2)
        rc = col_off + box_w - 1

        row = start_row
        # Top border
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}┌{'─' * (inner_w)}┐{_R}")
        row += 1
        # Title
        t_text = f" {title}"
        t_padded = t_text[:inner_w].ljust(inner_w)
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}│{_PICK_BG}\033[1m\033[97m{t_padded}\033[22m{_BD}│{_R}")
        row += 1
        # Sep
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}├{'─' * (inner_w)}┤{_R}")
        row += 1

        # Options
        avail_text = inner_w - 4  # "▸ " or "  " prefix (2) + right pad (2)
        for vi, opt_idx in enumerate(vis_range):
            is_sel = (opt_idx == cursor)
            # Truncate text to fit
            text = options[opt_idx]
            if len(text) > avail_text:
                text = text[:avail_text - 1] + "…"
            text_padded = text.ljust(avail_text)

            # Draw filled line
            sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}│{_PICK_BG}{' ' * inner_w}{_BD}│{_R}")
            # Draw content
            sys.stdout.write(f"\033[{row};{col_off + 1}H{_PICK_BG}")
            if is_sel:
                sys.stdout.write(f" \033[93m▸ \033[7m\033[97m{text_padded}\033[27m\033[22m")
            else:
                sys.stdout.write(f"   \033[37m{text_padded}\033[0m")
            # Right border
            sys.stdout.write(f"\033[{row};{rc}H{_PICK_BG}{_BD}│{_R}")
            row += 1

        # Scroll indicators
        if len(options) > max_visible:
            info = f" {cursor + 1}/{len(options)} "
            if scroll_top > 0:
                info = "↑" + info
            if scroll_top + max_visible < len(options):
                info = info + "↓"
            info_padded = info.center(inner_w)
            sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}│{_PICK_BG}\033[2m{info_padded}\033[22m{_BD}│{_R}")
            row += 1

        # Bottom
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}└{'─' * (inner_w)}┘{_R}")
        row += 1
        # Help
        help_text = "↑↓: move  Enter: select  Esc: cancel"
        help_col = max(1, (tw - len(help_text)) // 2)
        sys.stdout.write(f"\033[{row};{help_col}H\033[2m{help_text}\033[0m")
        sys.stdout.flush()

    # Clear + hide cursor
    sys.stdout.write("\033[2J\033[H\033[?25l")
    sys.stdout.flush()

    result = None
    try:
        while True:
            _draw()
            key = _read_key()
            if key == "ESC":
                break
            elif key == "UP":
                cursor = (cursor - 1) % len(options)
            elif key == "DOWN" or key == "TAB":
                cursor = (cursor + 1) % len(options)
            elif key == "ENTER":
                result = cursor
                break
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception:
        # Non-interactive stdin (piped input) or termios error — can't read keys
        pass
    finally:
        sys.stdout.write("\033[?25h\033[2J\033[H")
        sys.stdout.flush()

    return result


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
) -> Optional[Dict[str, str]]:
    """Display an interactive form. Returns dict of values or None if cancelled."""
    active_idx = 0
    total_items = len(fields) + 2  # fields + Accept + Cancel

    def _term_size():
        try:
            sz = os.get_terminal_size()
            return sz.columns, sz.lines
        except (ValueError, OSError):
            return 80, 24

    total_rows = len(fields) + 8  # top + title + sep + fields + sep + buttons + bottom + help

    def _draw():
        tw, th = _term_size()
        box_w = min(62, tw - 4)
        # Horizontal center offset (1-based column)
        col_off = max(1, (tw - box_w) // 2)
        # Vertical center
        start_row = max(1, (th - total_rows) // 2)
        # Right border column (absolute)
        rc = col_off + box_w - 1

        row = start_row
        # Top
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}┌{'─' * (box_w - 2)}┐{_RST}")
        row += 1
        # Title
        t = f" {title}"
        t_padded = t + " " * (box_w - 2 - len(t))
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}│{_FORM_BG}\033[1m\033[97m{t_padded}\033[22m{_BORDER_COLOR}│{_RST}")
        row += 1
        # Sep
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}├{'─' * (box_w - 2)}┤{_RST}")
        row += 1

        # Fields
        inner_w = box_w - 2  # usable chars between │ and │
        for i, field in enumerate(fields):
            is_active = (i == active_idx)
            indicator = "▸" if is_active else " "
            lbl = field.label + ":"
            lbl_padded = lbl.ljust(13)
            rendered = field.render(is_active)

            # Fill entire line background first
            _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}│{_FORM_BG}{' ' * inner_w}{_BORDER_COLOR}│{_RST}")
            # Now draw content over it (reposition after left border)
            _write(f"\033[{row};{col_off + 1}H{_FORM_BG}")
            if is_active:
                _write(f"\033[93m{indicator} \033[1m{lbl_padded}\033[22m\033[93m {rendered}{_FORM_BG}")
            else:
                _write(f" {indicator} \033[37m{lbl_padded}\033[0m{_FORM_BG} {rendered}{_FORM_BG}")
            # Ensure right border is intact
            _write(f"\033[{row};{rc}H{_FORM_BG}{_BORDER_COLOR}│{_RST}")
            row += 1

        # Sep
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}├{'─' * (box_w - 2)}┤{_RST}")
        row += 1

        # Buttons
        acc_idx = len(fields)
        can_idx = len(fields) + 1
        if active_idx == acc_idx:
            acc = f"\033[7m\033[92m Accept \033[27m\033[22m"
        else:
            acc = f"\033[32m Accept "
        if active_idx == can_idx:
            can = f"\033[7m\033[91m Cancel \033[27m\033[22m"
        else:
            can = f"\033[2m Cancel \033[22m"

        # Fill line then draw buttons
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}│{_FORM_BG}{' ' * inner_w}{_BORDER_COLOR}│{_RST}")
        _write(f"\033[{row};{col_off + 1}H{_FORM_BG}   {acc}{_FORM_BG}  {can}{_FORM_BG}")
        _write(f"\033[{row};{rc}H{_FORM_BG}{_BORDER_COLOR}│{_RST}")
        row += 1

        # Bottom
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}└{'─' * (box_w - 2)}┘{_RST}")
        row += 1

        # Help
        help_text = "↑↓/Tab: navigate  ←→/Space: options  Enter: accept  Esc: cancel"
        help_col = max(1, (tw - len(help_text)) // 2)
        _write(f"\033[{row};{help_col}H\033[2m{help_text}\033[0m")

        _flush()

    # Clear screen and hide cursor
    _write("\033[2J\033[H\033[?25l")
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
    except Exception as exc:
        import traceback
        try:
            with open("ttm_crash.log", "w", encoding="utf-8") as f:
                f.write(f"FORM LOOP ERROR:\n{traceback.format_exc()}")
        except Exception:
            pass
    finally:
        # Show cursor and clear screen for redraw
        _write("\033[?25h\033[2J\033[H")
        _flush()

    if cancelled:
        return None

    result = {}
    for field in fields:
        result[field.label] = field.get_value()
    return result
