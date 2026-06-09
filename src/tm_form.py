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
import signal
from typing import List, Optional, Dict, Any


# ─── Terminal capability detection ─────────────────────────────────────────────

_RESIZE_PENDING = False


def _on_sigwinch(*_):
    """Signal handler for terminal resize events."""
    global _RESIZE_PENDING
    _RESIZE_PENDING = True


# Register SIGWINCH handler (Unix only) for form redraw on resize
if hasattr(signal, "SIGWINCH"):
    signal.signal(signal.SIGWINCH, _on_sigwinch)


def _supports_unicode() -> bool:
    """Check if the terminal supports Unicode output."""
    encoding = getattr(sys.stdout, "encoding", "") or ""
    # Normalize: "UTF-8", "utf-8", "utf_8", "utf8" all → "utf8"
    normalized = encoding.lower().replace("-", "").replace("_", "")
    return normalized == "utf8"


# Box-drawing characters with ASCII fallback
if _supports_unicode():
    _BOX_TL = "┌"
    _BOX_TR = "┐"
    _BOX_BL = "└"
    _BOX_BR = "┘"
    _BOX_H = "─"
    _BOX_V = "│"
    _BOX_LT = "├"
    _BOX_RT = "┤"
    _ARROW_R = "▸"
    _ARROW_L = "◂"
    _ARROW_SEL = "▸"
    _CHECK_ON = "☑"
    _CHECK_OFF = "☐"
    _ELLIPSIS = "…"
    _SCROLL_UP = "↑"
    _SCROLL_DN = "↓"
else:
    _BOX_TL = "+"
    _BOX_TR = "+"
    _BOX_BL = "+"
    _BOX_BR = "+"
    _BOX_H = "-"
    _BOX_V = "|"
    _BOX_LT = "+"
    _BOX_RT = "+"
    _ARROW_R = ">"
    _ARROW_L = "<"
    _ARROW_SEL = ">"
    _CHECK_ON = "[x]"
    _CHECK_OFF = "[ ]"
    _ELLIPSIS = "~"
    _SCROLL_UP = "^"
    _SCROLL_DN = "v"


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
    import select as _select_mod

    def _read_key() -> str:
        """Read a single keypress on Unix/macOS.

        Handles multi-byte UTF-8 characters and uses select() for
        reliable escape sequence detection (works over SSH/tmux).
        """
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = os.read(fd, 1)
            if not ch:
                return ""
            if ch == b"\x1b":
                # Use select() to wait for escape sequence bytes —
                # more reliable than sleep, especially over SSH/tmux
                readable, _, _ = _select_mod.select([fd], [], [], 0.1)
                if readable:
                    import fcntl
                    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                    try:
                        try:
                            seq = os.read(fd, 10)
                        except (OSError, BlockingIOError):
                            seq = b""
                    finally:
                        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
                else:
                    seq = b""

                if seq == b"[A" or seq == b"OA":
                    return "UP"
                elif seq == b"[B" or seq == b"OB":
                    return "DOWN"
                elif seq == b"[C" or seq == b"OC":
                    return "RIGHT"
                elif seq == b"[D" or seq == b"OD":
                    return "LEFT"
                elif seq == b"[Z":
                    return "SHIFT_TAB"
                elif seq == b"[3~":
                    return "DELETE"
                elif seq in (b"[H", b"[1~", b"OH"):
                    return "HOME"
                elif seq in (b"[F", b"[4~", b"OF"):
                    return "END"
                elif seq == b"":
                    return "ESC"
                # Unknown escape sequence (e.g. Ctrl+Arrow) — ignore
                return ""
            if ch == b"\r" or ch == b"\n":
                return "ENTER"
            if ch == b"\t":
                return "TAB"
            if ch == b"\x7f" or ch == b"\x08":
                return "BACKSPACE"
            if len(ch) == 1 and ch[0] < 32:
                return ""
            # ── Multi-byte UTF-8 handling ──────────────────────────────
            # Determine how many continuation bytes are needed based on
            # the leading bits of the first byte.
            first_byte = ch[0]
            if first_byte < 0x80:
                # ASCII — single byte
                return ch.decode("utf-8", errors="replace")
            elif first_byte < 0xC0:
                # Unexpected continuation byte — discard
                return ""
            elif first_byte < 0xE0:
                remaining = 1  # 2-byte sequence (e.g. ñ, á, é)
            elif first_byte < 0xF0:
                remaining = 2  # 3-byte sequence (e.g. CJK)
            else:
                remaining = 3  # 4-byte sequence (e.g. emoji)
            # Read the remaining continuation bytes
            for _ in range(remaining):
                readable, _, _ = _select_mod.select([fd], [], [], 0.05)
                if readable:
                    extra = os.read(fd, 1)
                    if extra:
                        ch += extra
                    else:
                        break
                else:
                    break
            return ch.decode("utf-8", errors="replace")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# ─── Form Field Types ──────────────────────────────────────────────────────────

class TextField:
    """Editable text input field with visual line wrapping."""

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
        elif key == "HOME":
            self.cursor_pos = 0
        elif key == "END":
            self.cursor_pos = len(self.value)
        elif len(key) == 1 and key.isprintable():
            self.value = self.value[: self.cursor_pos] + key + self.value[self.cursor_pos:]
            self.cursor_pos += 1

    def line_count(self, width: int) -> int:
        """How many visual rows this field needs at the given width."""
        if width <= 0:
            return 1
        text = self.value or self.placeholder
        if not text:
            return 1
        return max(1, -(-len(text) // width))  # ceil division

    def render_lines(self, active: bool, width: int) -> list:
        """Return list of rendered strings, one per visual row."""
        if width <= 0:
            return [self.render(active)]

        text = self.value
        if not active and not text:
            return [f"\033[2m\033[37m{self.placeholder[:width]}\033[0m"]

        if not text:
            # Active with empty value — just show cursor
            return [f"\033[7m \033[27m\033[0m"]

        # Split text into chunks of `width`
        chunks = []
        for i in range(0, len(text), width):
            chunks.append(text[i:i + width])
        if not chunks:
            chunks = [""]

        if not active:
            return [f"\033[97m{c}\033[0m" for c in chunks]

        # Active — place cursor highlight in the correct chunk
        cursor_row = self.cursor_pos // width
        cursor_col = self.cursor_pos % width

        lines = []
        for row_idx, chunk in enumerate(chunks):
            if row_idx == cursor_row:
                before = chunk[:cursor_col]
                cursor_ch = chunk[cursor_col] if cursor_col < len(chunk) else " "
                after = chunk[cursor_col + 1:] if cursor_col < len(chunk) else ""
                lines.append(f"\033[97m{before}\033[7m{cursor_ch}\033[27m{after}\033[0m")
            else:
                lines.append(f"\033[97m{chunk}\033[0m")

        # If cursor is exactly at end (past last char), it's on a new line
        if self.cursor_pos == len(text) and len(text) % width == 0 and len(text) > 0:
            lines.append(f"\033[7m \033[27m\033[0m")

        return lines

    def render(self, active: bool) -> str:
        """Single-line render (fallback, used if width unknown)."""
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
            left = _ARROW_L if len(self._options) > 1 else " "
            right = _ARROW_R if len(self._options) > 1 else " "
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
    multi: bool = False,
) -> Optional[Any]:
    """Full-screen vertical list picker with optional multi-select.

    Args:
        title: Header text.
        options: List of option strings.
        selected: Initial cursor position.
        multi: If True, allows checking multiple items (Space to toggle).

    Returns:
        - multi=False: selected index (int) or None if cancelled.
        - multi=True: list of selected indices or None if cancelled.

    Navigation: Up/Down to move, Space to check/uncheck (multi),
                Enter to accept, Esc to cancel.
    """

    def _term_size():
        try:
            sz = os.get_terminal_size()
            return sz.columns, sz.lines
        except (ValueError, OSError):
            return 80, 24

    cursor = max(0, min(selected, len(options) - 1))
    checked: set = set()  # indices of checked items (multi mode)

    _PICK_BG = "\033[48;5;235m"  # 256-color dark grey (universal)
    _BD = "\033[36m"
    _R = "\033[0m"

    def _draw():
        tw, th = _term_size()
        # Box width: fit content or cap at terminal width - 4
        # Extra 4 chars for checkbox prefix in multi mode
        extra = 4 if multi else 0
        max_opt_len = max((len(o) for o in options), default=10)
        box_w = min(max_opt_len + 8 + extra, tw - 4)
        inner_w = box_w - 2

        # How many options fit (leave room for title, borders, buttons, help)
        max_visible = th - 9  # top + title + sep + ... + sep + buttons + bottom + help
        if max_visible < 3:
            max_visible = 3

        # Scroll window
        if len(options) <= max_visible:
            scroll_top = 0
            vis_range = range(len(options))
        else:
            half = max_visible // 2
            scroll_top = cursor - half
            if scroll_top < 0:
                scroll_top = 0
            if scroll_top + max_visible > len(options):
                scroll_top = len(options) - max_visible
            vis_range = range(scroll_top, scroll_top + max_visible)

        total_rows = len(list(vis_range)) + 8
        col_off = max(1, (tw - box_w) // 2)
        start_row = max(1, (th - total_rows) // 2)
        rc = col_off + box_w - 1

        row = start_row
        # Top border
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_TL}{_BOX_H * inner_w}{_BOX_TR}{_R}")
        row += 1
        # Title
        t_text = f" {title}"
        t_padded = t_text[:inner_w].ljust(inner_w)
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_V}{_PICK_BG}\033[1m\033[97m{t_padded}\033[22m{_BD}{_BOX_V}{_R}")
        row += 1
        # Sep
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_LT}{_BOX_H * inner_w}{_BOX_RT}{_R}")
        row += 1

        # Options
        # Available text: inner_w - cursor_indicator(3) - checkbox - right_pad(1)
        check_w = (len(_CHECK_ON) + 1) if multi else 0  # checkbox char(s) + space
        avail_text = inner_w - 4 - check_w
        for opt_idx in vis_range:
            is_cur = (opt_idx == cursor)
            is_chk = opt_idx in checked

            # Truncate text
            text = options[opt_idx]
            if len(text) > avail_text:
                text = text[:avail_text - 1] + _ELLIPSIS
            text_padded = text.ljust(avail_text)

            # Build checkbox string
            if multi:
                if is_chk:
                    chk = f"\033[92m{_CHECK_ON} \033[0m" + _PICK_BG
                else:
                    chk = f"\033[2m{_CHECK_OFF} \033[22m"
            else:
                chk = ""

            # Draw filled line
            sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_V}{_PICK_BG}{' ' * inner_w}{_BD}{_BOX_V}{_R}")
            # Draw content
            sys.stdout.write(f"\033[{row};{col_off + 1}H{_PICK_BG}")
            if is_cur:
                sys.stdout.write(f" \033[93m{_ARROW_SEL} {chk}\033[7m\033[97m{text_padded}\033[27m\033[22m")
            else:
                sys.stdout.write(f"   {chk}\033[37m{text_padded}\033[0m")
            # Right border
            sys.stdout.write(f"\033[{row};{rc}H{_PICK_BG}{_BD}{_BOX_V}{_R}")
            row += 1

        # Scroll indicators
        if len(options) > max_visible:
            scroll_top_actual = list(vis_range)[0] if vis_range else 0
            info = f" {cursor + 1}/{len(options)} "
            if scroll_top_actual > 0:
                info = _SCROLL_UP + info
            if scroll_top_actual + max_visible < len(options):
                info = info + _SCROLL_DN
            info_padded = info.center(inner_w)
            sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_V}{_PICK_BG}\033[2m{info_padded}\033[22m{_BD}{_BOX_V}{_R}")
            row += 1

        # Sep before buttons
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_LT}{_BOX_H * inner_w}{_BOX_RT}{_R}")
        row += 1

        # Buttons row
        if multi:
            n_sel = len(checked)
            accept_label = f" Accept ({n_sel}) " if n_sel else " Accept "
        else:
            accept_label = " Accept "
        cancel_label = " Cancel "

        btn_line = f"  \033[32m\033[7m{accept_label}\033[27m\033[0m{_PICK_BG}  \033[2m{cancel_label}\033[22m"
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_V}{_PICK_BG}{' ' * inner_w}{_BD}{_BOX_V}{_R}")
        sys.stdout.write(f"\033[{row};{col_off + 1}H{_PICK_BG}{btn_line}")
        sys.stdout.write(f"\033[{row};{rc}H{_PICK_BG}{_BD}{_BOX_V}{_R}")
        row += 1

        # Bottom
        sys.stdout.write(f"\033[{row};{col_off}H{_PICK_BG}{_BD}{_BOX_BL}{_BOX_H * inner_w}{_BOX_BR}{_R}")
        row += 1

        # Help
        if multi:
            help_text = f"{_SCROLL_UP}{_SCROLL_DN}: move  Space: check/uncheck  Enter: accept  Esc: cancel"
        else:
            help_text = f"{_SCROLL_UP}{_SCROLL_DN}: move  Enter: select  Esc: cancel"
        help_col = max(1, (tw - len(help_text)) // 2)
        sys.stdout.write(f"\033[{row};{help_col}H\033[2m{help_text}\033[0m")
        sys.stdout.flush()

    # Clear + hide cursor
    sys.stdout.write("\033[2J\033[H\033[?25l")
    sys.stdout.flush()

    result = None
    try:
        while True:
            # Handle terminal resize
            global _RESIZE_PENDING
            if _RESIZE_PENDING:
                _RESIZE_PENDING = False
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
            _draw()
            key = _read_key()
            if key == "ESC":
                break
            elif key == "UP":
                cursor = (cursor - 1) % len(options)
            elif key == "DOWN" or key == "TAB":
                cursor = (cursor + 1) % len(options)
            elif key == " " and multi:
                # Toggle checkbox
                if cursor in checked:
                    checked.discard(cursor)
                else:
                    checked.add(cursor)
            elif key == "ENTER":
                if multi:
                    result = sorted(checked) if checked else None
                else:
                    result = cursor
                break
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception:
        # Non-interactive stdin (piped input) or termios error
        pass
    finally:
        sys.stdout.write("\033[?25h\033[2J\033[H")
        sys.stdout.flush()

    return result


# ─── Form Display ──────────────────────────────────────────────────────────────

# Dark grey box background — uses 256-color for universal terminal support
_FORM_BG = "\033[48;5;235m"
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

    def _draw():
        tw, th = _term_size()
        box_w = min(62, tw - 4)
        inner_w = box_w - 2  # usable chars between │ and │
        # Width available for text values: inner - indicator(2) - label(13) - space(2)
        value_width = inner_w - 17

        # Calculate total field rows (some fields may wrap)
        total_field_rows = 0
        for f in fields:
            if isinstance(f, TextField):
                total_field_rows += f.line_count(value_width)
            else:
                total_field_rows += 1

        # Box dimensions: top + title + sep + field_rows + sep + buttons + bottom + help
        total_rows = total_field_rows + 7

        # Horizontal center offset (1-based column)
        col_off = max(1, (tw - box_w) // 2)
        # Vertical center
        start_row = max(1, (th - total_rows) // 2)
        # Right border column (absolute)
        rc = col_off + box_w - 1

        row = start_row
        # Top
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_TL}{_BOX_H * (box_w - 2)}{_BOX_TR}{_RST}")
        row += 1
        # Title
        t = f" {title}"
        t_padded = t + " " * (box_w - 2 - len(t))
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_FORM_BG}\033[1m\033[97m{t_padded}\033[22m{_BORDER_COLOR}{_BOX_V}{_RST}")
        row += 1
        # Sep
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_LT}{_BOX_H * (box_w - 2)}{_BOX_RT}{_RST}")
        row += 1

        # Fields
        for i, field in enumerate(fields):
            is_active = (i == active_idx)
            indicator = _ARROW_SEL if is_active else " "
            lbl = field.label + ":"
            lbl_padded = lbl.ljust(13)

            if isinstance(field, TextField):
                lines = field.render_lines(is_active, value_width)
                for line_idx, line_text in enumerate(lines):
                    # Fill background
                    _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_FORM_BG}{' ' * inner_w}{_BORDER_COLOR}{_BOX_V}{_RST}")
                    _write(f"\033[{row};{col_off + 1}H{_FORM_BG}")
                    if line_idx == 0:
                        # First line: show indicator + label
                        if is_active:
                            _write(f"\033[93m{indicator} \033[1m{lbl_padded}\033[22m\033[93m {line_text}{_FORM_BG}")
                        else:
                            _write(f" {indicator} \033[37m{lbl_padded}\033[0m{_FORM_BG} {line_text}{_FORM_BG}")
                    else:
                        # Continuation lines: indent to align with value
                        padding = " " * 17  # indicator(2) + label(13) + space(2)
                        _write(f"{padding}{line_text}{_FORM_BG}")
                    # Right border
                    _write(f"\033[{row};{rc}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_RST}")
                    row += 1
            else:
                rendered = field.render(is_active)
                # Fill background
                _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_FORM_BG}{' ' * inner_w}{_BORDER_COLOR}{_BOX_V}{_RST}")
                _write(f"\033[{row};{col_off + 1}H{_FORM_BG}")
                if is_active:
                    _write(f"\033[93m{indicator} \033[1m{lbl_padded}\033[22m\033[93m {rendered}{_FORM_BG}")
                else:
                    _write(f" {indicator} \033[37m{lbl_padded}\033[0m{_FORM_BG} {rendered}{_FORM_BG}")
                _write(f"\033[{row};{rc}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_RST}")
                row += 1

        # Sep
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_LT}{_BOX_H * (box_w - 2)}{_BOX_RT}{_RST}")
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
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_FORM_BG}{' ' * inner_w}{_BORDER_COLOR}{_BOX_V}{_RST}")
        _write(f"\033[{row};{col_off + 1}H{_FORM_BG}   {acc}{_FORM_BG}  {can}{_FORM_BG}")
        _write(f"\033[{row};{rc}H{_FORM_BG}{_BORDER_COLOR}{_BOX_V}{_RST}")
        row += 1

        # Bottom
        _write(f"\033[{row};{col_off}H{_FORM_BG}{_BORDER_COLOR}{_BOX_BL}{_BOX_H * (box_w - 2)}{_BOX_BR}{_RST}")
        row += 1

        # Help
        help_text = f"{_SCROLL_UP}{_SCROLL_DN}/Tab: navigate  <>/Space: options  Enter: accept  Esc: cancel"
        help_col = max(1, (tw - len(help_text)) // 2)
        _write(f"\033[{row};{help_col}H\033[2m{help_text}\033[0m")

        _flush()

    # Clear screen and hide cursor
    _write("\033[2J\033[H\033[?25l")
    _flush()

    # Save and install SIGTSTP handler for clean suspend/resume (B6)
    _prev_sigtstp = None
    _prev_sigcont = None
    if hasattr(signal, "SIGTSTP"):
        def _on_sigtstp(*_):
            # Restore terminal before suspending
            _write("\033[?25h\033[0m\033[2J\033[H")
            _flush()
            # Re-raise with default handler to actually suspend
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTSTP)

        def _on_sigcont(*_):
            # Re-register our handler and trigger redraw
            signal.signal(signal.SIGTSTP, _on_sigtstp)
            global _RESIZE_PENDING
            _RESIZE_PENDING = True

        _prev_sigtstp = signal.getsignal(signal.SIGTSTP)
        signal.signal(signal.SIGTSTP, _on_sigtstp)
        _prev_sigcont = signal.getsignal(signal.SIGCONT)
        signal.signal(signal.SIGCONT, _on_sigcont)

    cancelled = True
    try:
        while True:
            # Handle terminal resize (B5)
            global _RESIZE_PENDING
            if _RESIZE_PENDING:
                _RESIZE_PENDING = False
                _write("\033[2J\033[H")
                _flush()
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
            with open("src/ttm_crash.log", "w", encoding="utf-8") as f:
                f.write(f"FORM LOOP ERROR:\n{traceback.format_exc()}")
        except Exception:
            pass
    finally:
        # Restore signal handlers
        if hasattr(signal, "SIGTSTP") and _prev_sigtstp is not None:
            signal.signal(signal.SIGTSTP, _prev_sigtstp)
            signal.signal(signal.SIGCONT, _prev_sigcont)
        # Show cursor and clear screen for redraw
        _write("\033[?25h\033[2J\033[H")
        _flush()

    if cancelled:
        return None

    result = {}
    for field in fields:
        result[field.label] = field.get_value()
    return result
