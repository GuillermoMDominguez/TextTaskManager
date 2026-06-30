"""Journal I/O — atomic file writes, snapshots, and linting."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .tm_config import VALID_STATES
from .tm_journal_hooks import _notify_post_write, file_lock
from .tm_journal_parser import (
    _parse_due_value,
    _parse_priority_value,
    parse_date,
    parse_subtask_line,
    parse_task_line,
)


def _read_lines(filepath: str) -> List[str]:
    with open(filepath, "r", encoding="utf-8") as file_handle:
        return file_handle.readlines()


def _write_lines(filepath: str, lines: List[str]) -> None:
    """Atomically write lines to a journal file (write-tmp + rename)."""
    tmp = filepath + ".tmp"
    with file_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, filepath)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    _notify_post_write()


def write_journal(filepath: str, content: str) -> None:
    """Atomically write full text content to a journal file.

    Use this instead of Path(...).write_text() for any journal writes.
    Ensures atomic write (tmp+rename) and triggers post-write hooks.
    """
    tmp = filepath + ".tmp"
    with file_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, filepath)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    _notify_post_write()


def read_journal_snapshot(filepath: str) -> Optional[str]:
    """Return full journal file text for undo snapshots."""
    try:
        return Path(filepath).read_text(encoding="utf-8")
    except OSError:
        return None


def restore_journal_snapshot(filepath: str, snapshot: str) -> bool:
    """Restore full journal file text from an undo snapshot."""
    try:
        write_journal(filepath, snapshot)
        return True
    except OSError:
        return False


def lint_journal(filepath: str) -> List[str]:
    """Validate journal structure and return human-readable lint findings."""
    findings: List[str] = []

    try:
        lines = _read_lines(filepath)
    except Exception:
        return [f"Could not read journal: {filepath}"]

    has_parent_in_section = False

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("##"):
            if parse_date(line) is None:
                findings.append(f"Line {idx}: invalid date header format (expected ## dd/mm/yyyy).")
            has_parent_in_section = False
            continue

        if stripped.startswith(":"):
            if not has_parent_in_section:
                findings.append(f"Line {idx}: note without parent task.")
            continue

        if stripped.startswith("+"):
            if not has_parent_in_section:
                findings.append(f"Line {idx}: subtask without parent task.")
                continue
            subtask = parse_subtask_line(line)
            if subtask is None:
                findings.append(f"Line {idx}: invalid subtask format.")
            continue

        if stripped.startswith("-") and not stripped.startswith("--"):
            has_parent_in_section = True
            task = parse_task_line(line)
            if task is None or not task.title:
                findings.append(f"Line {idx}: invalid task format.")
                continue

            if task.state not in VALID_STATES:
                findings.append(f"Line {idx}: invalid state '{task.state}'.")

            due_meta = re.findall(r"(?:^|\s)--\s*(?:due|d)\s*[:=]\s*([^\s]+)", line, flags=re.IGNORECASE)
            for raw_due in due_meta:
                if _parse_due_value(raw_due) is None:
                    findings.append(f"Line {idx}: invalid due date '{raw_due}' (use dd/mm/yyyy).")

            priority_meta = re.findall(r"(?:^|\s)--\s*(?:priority|prio|p)\s*[:=]\s*([^\s]+)", line, flags=re.IGNORECASE)
            for raw_priority in priority_meta:
                if _parse_priority_value(raw_priority) is None:
                    findings.append(f"Line {idx}: invalid priority '{raw_priority}'.")
            continue

        findings.append(f"Line {idx}: unrecognized line format.")

    return findings
