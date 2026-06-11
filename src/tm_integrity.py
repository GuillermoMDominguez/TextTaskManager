"""Journal integrity checker with optional auto-fix.

Validates: date headers, task states, priorities, due dates, recurrences,
orphan subtasks/notes, malformed lines, duplicate blank lines, trailing whitespace.

Auto-fix repairs common issues from manual editing without losing data.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .tm_config import VALID_STATES, VALID_PRIORITIES, VALID_RECURRENCES
from .tm_journal import _parse_due_value, write_journal


# Recurrence values accepted by the system (reference from tm_config)
_VALID_RECURRENCES = set(VALID_RECURRENCES)

# Pattern to match a task line (starts with - but not --)
_TASK_RE = re.compile(r"^-\s+(.+)")
# Pattern to extract priority
_PRIORITY_RE = re.compile(r"--\s*(?:priority|prio|p)\s*[:=]\s*(\S+)", re.IGNORECASE)
# Pattern to extract due date
_DUE_RE = re.compile(r"--\s*(?:due|d)\s*[:=]\s*(\S+)", re.IGNORECASE)
# Pattern to extract recurrence
_RECUR_RE = re.compile(r"--\s*(?:recur|rec|r)\s*[:=]\s*(\S+)", re.IGNORECASE)
# Pattern for date header
_DATE_HEADER_RE = re.compile(r"^##\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$")


def check_and_fix_journal(filepath: str, *, auto_fix: bool = False) -> Tuple[List[str], int]:
    """Run integrity checks on a journal file.

    Returns (list_of_issues, number_of_fixes_applied).
    If auto_fix is False, fixes_applied is always 0.
    """
    path = Path(filepath)
    if not path.exists():
        return ([f"Journal file not found: {filepath}"], 0)

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ([f"Could not read journal: {exc}"], 0)

    lines = content.split("\n")
    issues: List[str] = []
    fixed_lines: List[str] = []
    fixes_applied = 0
    has_parent_in_section = False
    last_was_blank = False
    in_valid_section = False  # True once we've seen at least one date header

    for idx, line in enumerate(lines):
        stripped = line.strip()
        line_num = idx + 1

        # ─── Consecutive blank lines ──────────────────────────────────
        if not stripped:
            if last_was_blank:
                issues.append(f"Line {line_num}: consecutive blank line (removed).")
                if auto_fix:
                    fixes_applied += 1
                    continue  # skip this blank line
            last_was_blank = True
            fixed_lines.append(line)
            continue
        last_was_blank = False

        # ─── Trailing whitespace ──────────────────────────────────────
        if line != line.rstrip():
            issues.append(f"Line {line_num}: trailing whitespace (trimmed).")
            if auto_fix:
                line = line.rstrip()
                fixes_applied += 1

        # ─── Date headers ─────────────────────────────────────────────
        if stripped.startswith("##"):
            has_parent_in_section = False
            in_valid_section = True
            if not _DATE_HEADER_RE.match(stripped):
                # Try to salvage a date from the line
                salvaged = _try_fix_date_header(stripped)
                if salvaged and auto_fix:
                    issues.append(f"Line {line_num}: malformed date header '{stripped}' -> '{salvaged}'.")
                    line = salvaged
                    fixes_applied += 1
                else:
                    issues.append(f"Line {line_num}: invalid date header format (expected ## dd/mm/yyyy).")
            else:
                # Validate the date is actually real
                date_str = _DATE_HEADER_RE.match(stripped).group(1)
                if not _is_valid_date(date_str):
                    issues.append(f"Line {line_num}: date header has invalid date '{date_str}'.")
            fixed_lines.append(line)
            continue

        # ─── Lines before any date header ─────────────────────────────
        if not in_valid_section:
            # Could be a comment or metadata at top of file - leave as-is
            fixed_lines.append(line)
            continue

        # ─── Notes ────────────────────────────────────────────────────
        if stripped.startswith(":"):
            if not has_parent_in_section:
                issues.append(f"Line {line_num}: note without parent task.")
            fixed_lines.append(line)
            continue

        # ─── Subtasks ─────────────────────────────────────────────────
        if stripped.startswith("+"):
            if not has_parent_in_section:
                issues.append(f"Line {line_num}: subtask without parent task.")
            else:
                _check_subtask(stripped, line_num, issues)
            fixed_lines.append(line)
            continue

        # ─── Tasks ────────────────────────────────────────────────────
        if stripped.startswith("-") and not stripped.startswith("--"):
            has_parent_in_section = True
            result = _check_task_line(stripped, line_num, issues, auto_fix)
            if result is not None and auto_fix:
                line = result
                fixes_applied += 1
            fixed_lines.append(line)
            continue

        # ─── Metadata continuation lines (-- key:value) ───────────────
        if stripped.startswith("--") and not stripped.startswith("---"):
            if not has_parent_in_section:
                issues.append(f"Line {line_num}: metadata line without parent task.")
            fixed_lines.append(line)
            continue

        # ─── Unrecognized ─────────────────────────────────────────────
        # If inside a task block, treat as title continuation (tags, wrapped text)
        if has_parent_in_section:
            fixed_lines.append(line)
            continue

        issues.append(f"Line {line_num}: unrecognized line format.")
        fixed_lines.append(line)

    # Write fixed content back (atomically via write_journal)
    if auto_fix and fixes_applied > 0:
        fixed_content = "\n".join(fixed_lines)
        try:
            write_journal(filepath, fixed_content)
        except OSError:
            pass  # Non-fatal, we still report findings

    return (issues, fixes_applied if auto_fix else 0)


def _extract_state(line: str) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """Extract state from a task/subtask line by matching against known states.

    Returns (state, (start, end)) where start/end are the character positions
    of the state in the line, or (None, None) if no state found.
    If a segment looks like a state (all uppercase, no colon) but isn't valid,
    returns the raw text so it can be flagged as invalid.
    """
    # Metadata pattern: segments with key:value format (due:, priority:, recur:, etc.)
    _metadata_re = re.compile(r"^(?:due|d|priority|prio|p|recur|rec|r|spent|blockedby|blocks|jira)\s*[:=]", re.IGNORECASE)

    parts = line.split("--")
    offset = 0
    first_state_candidate = None
    first_candidate_pos = None

    for i, part in enumerate(parts):
        if i == 0:
            offset += len(part)
            continue
        candidate = part.strip()
        candidate_upper = candidate.upper()

        # Skip metadata segments (key:value)
        if _metadata_re.match(candidate):
            offset += 2 + len(part)
            continue

        # Check against valid states
        for state in VALID_STATES:
            if candidate_upper == state or candidate_upper.startswith(state + " "):
                sep_pos = offset
                state_start = line.index(candidate[:len(state)], sep_pos)
                state_end = state_start + len(state)
                return (candidate[:len(state)], (state_start, state_end))

        # If it looks like a state (all uppercase letters/spaces/underscores, no colon)
        # but didn't match any valid state, flag it
        if first_state_candidate is None and re.match(r"^[A-Z][A-Z_ ]*$", candidate):
            sep_pos = offset
            state_start = line.index(candidate, sep_pos)
            state_end = state_start + len(candidate)
            first_state_candidate = candidate
            first_candidate_pos = (state_start, state_end)

        offset += 2 + len(part)

    # Return the first state-like candidate that wasn't valid (so it gets flagged)
    if first_state_candidate is not None:
        return (first_state_candidate, first_candidate_pos)

    return (None, None)


def _check_task_line(stripped: str, line_num: int, issues: List[str], auto_fix: bool) -> Optional[str]:
    """Validate a task line. Returns fixed line if auto_fix needed, else None."""
    fixed = None

    # Check state
    state_raw, state_pos = _extract_state(stripped)
    if state_raw is not None:
        if state_raw not in VALID_STATES:
            # Try case-insensitive match
            upper = state_raw.upper()
            if upper in VALID_STATES and auto_fix:
                issues.append(f"Line {line_num}: state '{state_raw}' -> '{upper}'.")
                start, end = state_pos
                stripped = stripped[:start] + upper + stripped[end:]
                fixed = stripped
            else:
                issues.append(f"Line {line_num}: invalid state '{state_raw}'.")

    # Check priorities
    for m in _PRIORITY_RE.finditer(stripped):
        raw = m.group(1)
        if raw.upper() not in VALID_PRIORITIES and raw.lower() not in {p.lower() for p in VALID_PRIORITIES}:
            issues.append(f"Line {line_num}: invalid priority '{raw}'.")

    # Check due dates
    for m in _DUE_RE.finditer(stripped):
        raw = m.group(1)
        if _parse_due_value(raw) is None:
            issues.append(f"Line {line_num}: invalid due date '{raw}' (use dd/mm/yyyy).")

    # Check recurrences
    for m in _RECUR_RE.finditer(stripped):
        raw = m.group(1)
        if raw.lower() not in _VALID_RECURRENCES:
            issues.append(f"Line {line_num}: invalid recurrence '{raw}'.")

    return fixed


def _check_subtask(stripped: str, line_num: int, issues: List[str]) -> None:
    """Validate subtask format."""
    # Minimal: + title -- STATE
    # The title should be non-empty after the +
    content = stripped[1:].strip()
    if not content:
        issues.append(f"Line {line_num}: empty subtask.")
        return

    # Check state if present
    state_raw, _ = _extract_state(stripped)
    if state_raw is not None:
        if state_raw not in VALID_STATES:
            issues.append(f"Line {line_num}: subtask has invalid state '{state_raw}'.")

    # Check due date if present
    for m in _DUE_RE.finditer(stripped):
        raw = m.group(1)
        if _parse_due_value(raw) is None:
            issues.append(f"Line {line_num}: subtask has invalid due date '{raw}'.")


def _try_fix_date_header(stripped: str) -> Optional[str]:
    """Attempt to extract a valid date from a malformed header."""
    # Try to find a date pattern anywhere in the line
    match = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", stripped)
    if match:
        d, m, y = match.group(1), match.group(2), match.group(3)
        candidate = f"## {d.zfill(2)}/{m.zfill(2)}/{y}"
        if _is_valid_date(f"{d.zfill(2)}/{m.zfill(2)}/{y}"):
            return candidate
    return None


def _is_valid_date(date_str: str) -> bool:
    """Check if dd/mm/yyyy or dd/mm/yy is a real calendar date."""
    try:
        datetime.strptime(date_str, "%d/%m/%Y")
        return True
    except ValueError:
        pass
    try:
        datetime.strptime(date_str, "%d/%m/%y")
        return True
    except ValueError:
        return False
