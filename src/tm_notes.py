"""Notes management — standalone .md notes in journals/notes/.

Notes are stored as individual .md files, organized in subdirectories.
Tasks can link to notes via metadata (-- notes:path/to/note.md).
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .tm_journal import _notify_post_write, parse_journal, update_task_metadata_in_file, update_subtask_metadata_in_file
from .tm_log import log as tm_log
from .tm_models import Task, Subtask
from .tm_ui import Colors


_NOTES_DIR_NAME = "notes"


def _notes_dir(journal_path: str) -> Path:
    return Path(journal_path).resolve().parent / _NOTES_DIR_NAME


def ensure_notes_dir(journal_path: str) -> Path:
    nd = _notes_dir(journal_path)
    nd.mkdir(parents=True, exist_ok=True)
    return nd


def _sanitize_filename(title: str) -> str:
    name = title.strip().lower()
    name = re.sub(r"[^a-z0-9\u00f1\u00e1-\u00fa._-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    if not name:
        name = "untitled"
    return name + ".md"


def resolve_note_path(journal_path: str, note_path_str: str) -> Optional[Path]:
    nd = _notes_dir(journal_path)
    if not nd.exists():
        return None
    candidate = nd / note_path_str
    if candidate.exists() and candidate.is_file():
        return candidate
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def resolve_or_create_note_path(journal_path: str, note_path_str: str) -> Path:
    existing = resolve_note_path(journal_path, note_path_str)
    if existing:
        return existing
    nd = ensure_notes_dir(journal_path)
    candidate = nd / note_path_str
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def list_note_folders(journal_path: str) -> List[str]:
    nd = _notes_dir(journal_path)
    if not nd.exists():
        return []
    folders: set = set()
    for f in sorted(nd.rglob("*.md")):
        if f.is_file():
            rel_dir = f.relative_to(nd).parent
            if str(rel_dir) != ".":
                folders.add(str(rel_dir))
    return sorted(folders)


def list_notes(journal_path: str, folder: Optional[str] = None) -> List[dict]:
    nd = _notes_dir(journal_path)
    if not nd.exists():
        return []
    if folder:
        scan_dir = nd / folder
        if not scan_dir.exists() or not scan_dir.is_dir():
            return []
    else:
        scan_dir = nd
    notes = []
    for f in sorted(scan_dir.rglob("*.md")):
        if f.is_file():
            rel = str(f.relative_to(nd))
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                mtime = datetime.now()
            preview = ""
            try:
                first_line = f.read_text("utf-8", errors="replace").strip().split("\n")[0]
                preview = first_line[:80]
            except OSError:
                pass
            notes.append({
                "path": rel,
                "name": f.stem,
                "mtime": mtime.isoformat(),
                "preview": preview,
            })
    return notes


def read_note(journal_path: str, note_path_str: str) -> Optional[str]:
    resolved = resolve_note_path(journal_path, note_path_str)
    if resolved is None:
        return None
    try:
        return resolved.read_text("utf-8", errors="replace")
    except OSError:
        return None


def write_note(journal_path: str, note_path_str: str, content: str) -> bool:
    resolved = resolve_or_create_note_path(journal_path, note_path_str)
    try:
        resolved.write_text(content, "utf-8")
        _notify_post_write()
        return True
    except OSError:
        return False


def delete_note(journal_path: str, note_path_str: str) -> bool:
    resolved = resolve_note_path(journal_path, note_path_str)
    if resolved is None:
        return False
    # Auto-unlink from all tasks and subtasks before deleting
    try:
        tasks_by_date = parse_journal(journal_path)
        for date_tasks in tasks_by_date.values():
            for task in date_tasks:
                if note_path_str in task.linked_notes:
                    current = [n for n in task.linked_notes if n != note_path_str]
                    _notes_val = ",".join(current) if current else ""
                    update_task_metadata_in_file(
                        journal_path, task,
                        due_date=task.due_date,
                        priority=task.priority,
                        recurrence=task.recurrence,
                        jira_key=task.jira_key,
                        notes=_notes_val,
                    )
                    task.linked_notes = current
                for st in task.subtasks:
                    if note_path_str in st.linked_notes:
                        current = [n for n in st.linked_notes if n != note_path_str]
                        _notes_val = ",".join(current) if current else ""
                        update_subtask_metadata_in_file(
                            journal_path, st,
                            notes=_notes_val,
                        )
                        st.linked_notes = current
    except Exception:
        pass
    try:
        resolved.unlink()
        _remove_empty_parents(resolved.parent, _notes_dir(journal_path))
        _notify_post_write()
        return True
    except OSError:
        return False


def _remove_empty_parents(start: Path, stop_at: Path) -> None:
    p = start
    while p != stop_at and p != p.parent:
        try:
            if any(p.iterdir()):
                return
            p.rmdir()
        except OSError:
            return
        p = p.parent


def move_note(journal_path: str, from_path_str: str, to_path_str: str) -> bool:
    nd = _notes_dir(journal_path)
    src = resolve_note_path(journal_path, from_path_str)
    if src is None:
        return False
    dst = nd / to_path_str
    if not dst.suffix:
        dst = dst.with_suffix(".md")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dst)
        _remove_empty_parents(src.parent, nd)
        _notify_post_write()
        return True
    except OSError:
        return False


def open_editor(prefill: str = "") -> Optional[str]:
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            if prefill:
                f.write(prefill)
            tmp_path = f.name
    except OSError:
        tm_log("error", "Could not create temp file for editor.")
        return None

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or ""
    if not editor:
        for candidate in ("nano", "vim", "vi"):
            if _which(candidate):
                editor = candidate
                break
    if not editor:
        tm_log("error", "No EDITOR set and no editor found (nano/vim/vi).")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None

    try:
        subprocess.check_call([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return content
    except (subprocess.SubprocessError, OSError) as exc:
        tm_log("error", f"Editor failed: {exc}")
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _which(cmd: str) -> Optional[str]:
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(p) / cmd
        if candidate.exists():
            return str(candidate)
    return None


def create_note_interactive(journal_path: str, title: str) -> Optional[str]:
    nd = ensure_notes_dir(journal_path)
    note_path = nd / title
    if not note_path.suffix:
        note_path = note_path.with_suffix(".md")
    if note_path.exists():
        tm_log("error", f"Note already exists: {title}")
        return None
    note_path.parent.mkdir(parents=True, exist_ok=True)

    content = open_editor()
    if content is None:
        return None
    if not content.strip():
        tm_log("info", "Empty note, not created.")
        return None

    try:
        note_path.write_text(content, "utf-8")
        rel = str(note_path.relative_to(nd))
        tm_log("info", f"Note created: {rel}")
        return rel
    except OSError as exc:
        tm_log("error", f"Could not write note: {exc}")
        return None


def edit_note_interactive(journal_path: str, note_path_str: str) -> bool:
    existing = read_note(journal_path, note_path_str)
    if existing is None:
        tm_log("error", f"Note not found: {note_path_str}")
        return False
    content = open_editor(prefill=existing)
    if content is None:
        return False
    if content == existing:
        tm_log("info", "Note unchanged.")
        return True
    if write_note(journal_path, note_path_str, content):
        tm_log("info", f"Note updated: {note_path_str}")
        return True
    tm_log("error", "Could not write note.")
    return False


def link_note_to_task(journal_path: str, task: Task, note_path_str: str) -> bool:
    current = list(task.linked_notes)
    if note_path_str in current:
        return True
    current.append(note_path_str)
    return _update_task_notes(journal_path, task, current)


def unlink_note_from_task(journal_path: str, task: Task, note_path_str: str) -> bool:
    current = list(task.linked_notes)
    if note_path_str not in current:
        return True
    current.remove(note_path_str)
    return _update_task_notes(journal_path, task, current)


def _notes_metadata_value(linked_notes: List[str]) -> Optional[str]:
    if not linked_notes:
        return None
    return ",".join(linked_notes)


def _update_task_notes(journal_path: str, task: Task, linked_notes: List[str]) -> bool:
    if linked_notes:
        meta_val = ",".join(linked_notes)
    else:
        meta_val = ""
    success = update_task_metadata_in_file(
        journal_path, task,
        due_date=task.due_date,
        priority=task.priority,
        recurrence=task.recurrence,
        jira_key=task.jira_key,
        notes=meta_val,
    )
    if success:
        task.linked_notes = linked_notes
    return success


def get_linked_notes_content(journal_path: str, task: Task) -> List[dict]:
    result = []
    for note_ref in task.linked_notes:
        resolved = resolve_note_path(journal_path, note_ref)
        if resolved is None:
            result.append({"path": note_ref, "exists": False, "content": None})
            continue
        try:
            content = resolved.read_text("utf-8", errors="replace")
        except OSError:
            content = None
        result.append({"path": note_ref, "exists": True, "content": content})
    return result


def search_notes(journal_path: str, query: str) -> List[dict]:
    """Search notes by filename or content. Returns matches with context snippet."""
    nd = _notes_dir(journal_path)
    if not nd.exists():
        return []
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for f in sorted(nd.rglob("*.md")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(nd))
        name_lower = rel.lower()
        score = 0
        snippet = ""
        if q in name_lower:
            score += 2
        try:
            content = f.read_text("utf-8", errors="replace")
            if q in content.lower():
                score += 1
                idx = content.lower().find(q)
                start = max(0, idx - 40)
                end = min(len(content), idx + len(q) + 40)
                snippet = content[start:end].replace("\n", " ")
                if start > 0:
                    snippet = "…" + snippet
                if end < len(content):
                    snippet = snippet + "…"
        except OSError:
            continue
        if score > 0:
            results.append({
                "path": rel,
                "name": f.stem,
                "score": score,
                "snippet": snippet,
            })
    results.sort(key=lambda r: -r["score"])
    return results[:20]
