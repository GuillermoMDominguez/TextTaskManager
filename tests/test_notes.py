"""Tests for tm_notes.py — standalone .md note management."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.tm_notes import (
    ensure_notes_dir,
    resolve_note_path,
    resolve_or_create_note_path,
    list_note_folders,
    list_notes,
    read_note,
    write_note,
    delete_note,
    move_note,
    _sanitize_filename,
    _notes_dir,
    _remove_empty_parents,
    get_linked_notes_content,
    link_note_to_task,
    unlink_note_from_task,
)
from src.tm_models import Task


class TestSanitizeFilename(unittest.TestCase):
    def test_basic_lowercase(self):
        self.assertEqual(_sanitize_filename("Hello World"), "hello-world.md")

    def test_special_chars_replaced(self):
        self.assertEqual(_sanitize_filename("test@#$file"), "test-file.md")

    def test_accents_preserved(self):
        self.assertEqual(_sanitize_filename("mañana"), "mañana.md")

    def test_multi_dashes_collapsed(self):
        self.assertEqual(_sanitize_filename("a---b"), "a-b.md")

    def test_leading_trailing_dashes_stripped(self):
        self.assertEqual(_sanitize_filename("-hello-"), "hello.md")

    def test_dot_preserved(self):
        self.assertEqual(_sanitize_filename("my.note"), "my.note.md")

    def test_empty_fallsback(self):
        self.assertEqual(_sanitize_filename(""), "untitled.md")

    def test_whitespace_only_fallsback(self):
        self.assertEqual(_sanitize_filename("   "), "untitled.md")

    def test_underscore_preserved(self):
        self.assertEqual(_sanitize_filename("my_note"), "my_note.md")


class TestNotesDir(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "test.txt")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_notes_dir_path(self):
        nd = _notes_dir(self.journal_path)
        expected = Path(self.tmpdir.name).resolve() / "journals" / "notes"
        self.assertEqual(nd, expected)

    def test_ensure_notes_dir_creates(self):
        nd = ensure_notes_dir(self.journal_path)
        self.assertTrue(nd.exists())
        self.assertTrue(nd.is_dir())

    def test_ensure_notes_dir_idempotent(self):
        nd1 = ensure_notes_dir(self.journal_path)
        nd2 = ensure_notes_dir(self.journal_path)
        self.assertEqual(nd1, nd2)
        self.assertTrue(nd1.exists())


class TestResolveNotePath(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")
        ensure_notes_dir(self.journal_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_resolve_nonexistent_returns_none(self):
        self.assertIsNone(resolve_note_path(self.journal_path, "nonexistent.md"))

    def test_resolve_existing_file(self):
        nd = _notes_dir(self.journal_path)
        note = nd / "hello.md"
        note.write_text("content")
        resolved = resolve_note_path(self.journal_path, "hello.md")
        self.assertEqual(resolved, note)

    def test_resolve_without_suffix(self):
        nd = _notes_dir(self.journal_path)
        note = nd / "hello.md"
        note.write_text("content")
        resolved = resolve_note_path(self.journal_path, "hello")
        self.assertEqual(resolved, note)

    def test_resolve_nested_path(self):
        nd = _notes_dir(self.journal_path)
        nested = nd / "sub" / "note.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("content")
        resolved = resolve_note_path(self.journal_path, "sub/note.md")
        self.assertEqual(resolved, nested)

    def test_resolve_nonexistent_notes_dir(self):
        other = "/tmp/nonexistent_journal.txt"
        self.assertIsNone(resolve_note_path(other, "x.md"))


class TestResolveOrCreateNotePath(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_new_file(self):
        nd = _notes_dir(self.journal_path)
        path = resolve_or_create_note_path(self.journal_path, "new_note.md")
        # resolve_or_create_note_path creates the parent directory, not the file
        self.assertTrue(path.parent.exists())
        self.assertEqual(path, nd / "new_note.md")

    def test_create_without_suffix(self):
        nd = _notes_dir(self.journal_path)
        path = resolve_or_create_note_path(self.journal_path, "newnote")
        self.assertTrue(path.parent.exists())
        self.assertEqual(path, nd / "newnote.md")

    def test_returns_existing(self):
        nd = _notes_dir(self.journal_path)
        nd.mkdir(parents=True)
        existing = nd / "existing.md"
        existing.write_text("hello")
        path = resolve_or_create_note_path(self.journal_path, "existing.md")
        self.assertEqual(path, existing)

    def test_creates_parent_dirs(self):
        nd = _notes_dir(self.journal_path)
        path = resolve_or_create_note_path(self.journal_path, "a/b/c.md")
        self.assertTrue(path.parent.exists())
        self.assertEqual(path, nd / "a" / "b" / "c.md")


class TestListNoteFolders(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_empty_when_no_notes_dir(self):
        self.assertEqual(list_note_folders(self.journal_path), [])

    def test_flat_notes_no_folders(self):
        nd = ensure_notes_dir(self.journal_path)
        (nd / "a.md").write_text("x")
        (nd / "b.md").write_text("x")
        self.assertEqual(list_note_folders(self.journal_path), [])

    def test_folders_detected(self):
        nd = ensure_notes_dir(self.journal_path)
        (nd / "work" / "note1.md").parent.mkdir(parents=True)
        (nd / "work" / "note1.md").write_text("x")
        (nd / "personal" / "note2.md").parent.mkdir(parents=True)
        (nd / "personal" / "note2.md").write_text("x")
        self.assertEqual(list_note_folders(self.journal_path), ["personal", "work"])

    def test_nested_folders(self):
        nd = ensure_notes_dir(self.journal_path)
        (nd / "a" / "b" / "n.md").parent.mkdir(parents=True)
        (nd / "a" / "b" / "n.md").write_text("x")
        self.assertEqual(list_note_folders(self.journal_path), ["a/b"])


class TestListNotes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")
        ensure_notes_dir(self.journal_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_note(self, rel_path, content="Hello World"):
        nd = Path(self.tmpdir.name) / "journals" / "notes"
        full = nd / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return full

    def test_empty_when_no_notes(self):
        self.assertEqual(list_notes(self.journal_path), [])

    def test_lists_all_notes(self):
        self._make_note("a.md")
        self._make_note("b.md")
        notes = list_notes(self.journal_path)
        self.assertEqual(len(notes), 2)
        paths = [n["path"] for n in notes]
        self.assertIn("a.md", paths)
        self.assertIn("b.md", paths)

    def test_lists_in_folder(self):
        self._make_note("work/t1.md")
        self._make_note("work/t2.md")
        self._make_note("personal/p1.md")
        notes = list_notes(self.journal_path, folder="work")
        self.assertEqual(len(notes), 2)
        for n in notes:
            self.assertTrue(n["path"].startswith("work/"))

    def test_unknown_folder_returns_empty(self):
        self._make_note("a.md")
        self.assertEqual(list_notes(self.journal_path, folder="unknown"), [])

    def test_preview_from_first_line(self):
        self._make_note("preview.md", "# My Note\n\nSecond line")
        notes = list_notes(self.journal_path)
        self.assertEqual(notes[0]["preview"], "# My Note")

    def test_name_from_stem(self):
        self._make_note("my_note.md")
        notes = list_notes(self.journal_path)
        self.assertEqual(notes[0]["name"], "my_note")


class TestReadWriteNotes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")
        ensure_notes_dir(self.journal_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_write_and_read(self):
        self.assertTrue(write_note(self.journal_path, "test.md", "hello world"))
        content = read_note(self.journal_path, "test.md")
        self.assertEqual(content, "hello world")

    def test_read_nonexistent_returns_none(self):
        self.assertIsNone(read_note(self.journal_path, "nonexistent.md"))

    def test_write_overwrites(self):
        write_note(self.journal_path, "over.md", "first")
        write_note(self.journal_path, "over.md", "second")
        self.assertEqual(read_note(self.journal_path, "over.md"), "second")

    def test_write_nested(self):
        self.assertTrue(write_note(self.journal_path, "sub/dir/note.md", "nested"))
        self.assertEqual(read_note(self.journal_path, "sub/dir/note.md"), "nested")

    def test_write_without_suffix(self):
        self.assertTrue(write_note(self.journal_path, "nosuffix", "test"))
        self.assertEqual(read_note(self.journal_path, "nosuffix.md"), "test")

    def test_write_notifies_hooks(self):
        with patch("src.tm_notes._notify_post_write") as mock:
            write_note(self.journal_path, "test.md", "x")
            mock.assert_called_once()


class TestDeleteNote(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")
        ensure_notes_dir(self.journal_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_delete_existing(self):
        write_note(self.journal_path, "del.md", "to delete")
        self.assertTrue(delete_note(self.journal_path, "del.md"))
        self.assertIsNone(read_note(self.journal_path, "del.md"))

    def test_delete_nonexistent(self):
        self.assertFalse(delete_note(self.journal_path, "nonexistent.md"))

    def test_delete_removes_empty_parent(self):
        write_note(self.journal_path, "a/b/c/note.md", "deep")
        delete_note(self.journal_path, "a/b/c/note.md")
        self.assertFalse((_notes_dir(self.journal_path) / "a" / "b" / "c").exists())
        self.assertFalse((_notes_dir(self.journal_path) / "a" / "b").exists())
        self.assertFalse((_notes_dir(self.journal_path) / "a").exists())

    def test_delete_notifies_hooks(self):
        write_note(self.journal_path, "hooked.md", "x")
        with patch("src.tm_notes._notify_post_write") as mock:
            delete_note(self.journal_path, "hooked.md")
            mock.assert_called()

    @patch("src.tm_notes.parse_journal")
    @patch("src.tm_notes.update_task_metadata_in_file")
    def test_delete_unlinks_from_tasks(self, mock_update, mock_parse):
        mock_parse.return_value = {
            datetime(2026, 6, 1): [
                Task(title="T1", task_id="1", linked_notes=["note_to_del.md"]),
            ]
        }
        write_note(self.journal_path, "note_to_del.md", "x")
        delete_note(self.journal_path, "note_to_del.md")
        mock_update.assert_called()


class TestMoveNote(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = str(Path(self.tmpdir.name) / "journals" / "j.txt")
        ensure_notes_dir(self.journal_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_move(self):
        write_note(self.journal_path, "src.md", "hello")
        self.assertTrue(move_note(self.journal_path, "src.md", "dst.md"))
        self.assertIsNone(read_note(self.journal_path, "src.md"))
        self.assertEqual(read_note(self.journal_path, "dst.md"), "hello")

    def test_move_nonexistent(self):
        self.assertFalse(move_note(self.journal_path, "no.md", "dst.md"))

    def test_move_to_folder(self):
        write_note(self.journal_path, "src.md", "hello")
        self.assertTrue(move_note(self.journal_path, "src.md", "sub/dst.md"))
        self.assertEqual(read_note(self.journal_path, "sub/dst.md"), "hello")

    def test_move_notifies_hooks(self):
        write_note(self.journal_path, "src.md", "x")
        with patch("src.tm_notes._notify_post_write") as mock:
            move_note(self.journal_path, "src.md", "dst.md")
            mock.assert_called_once()


class TestRemoveEmptyParents(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_removes_empty(self):
        p = self.base / "a" / "b" / "c"
        p.mkdir(parents=True)
        _remove_empty_parents(p, self.base)
        self.assertFalse(p.exists())
        self.assertFalse((self.base / "a" / "b").exists())
        self.assertFalse((self.base / "a").exists())

    def test_stops_at_stop_at(self):
        stop = self.base / "stop"
        p = stop / "a" / "b"
        p.mkdir(parents=True)
        _remove_empty_parents(p, stop)
        self.assertFalse(p.exists())
        self.assertFalse((stop / "a").exists())
        self.assertTrue(stop.exists())

    def test_does_not_remove_nonempty(self):
        p = self.base / "a" / "b"
        p.mkdir(parents=True)
        (self.base / "a" / "other.txt").write_text("x")
        _remove_empty_parents(p, self.base)
        self.assertFalse(p.exists())
        self.assertTrue((self.base / "a").exists())

    def test_stops_at_root(self):
        p = self.base / "a"
        p.mkdir(parents=True)
        _remove_empty_parents(p, p.parent)
        self.assertFalse(p.exists())


class TestLinkedNotes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir(parents=True)
        self.journal_path = str(jdir / "j.txt")
        Path(self.journal_path).write_text("## 01/06/2026\n- placeholder -- TODO\n")
        ensure_notes_dir(self.journal_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _first_task(self):
        from src.tm_journal import add_task_to_file, parse_journal
        add_task_to_file(self.journal_path, "Test", state="TODO")
        tasks = parse_journal(self.journal_path)
        return list(tasks.values())[0][0]

    def test_link_note_to_task(self):
        task = self._first_task()
        self.assertTrue(link_note_to_task(self.journal_path, task, "note.md"))

    def test_unlink_note_from_task(self):
        task = self._first_task()
        task.linked_notes = ["note.md"]
        self.assertTrue(unlink_note_from_task(self.journal_path, task, "note.md"))
        self.assertEqual(task.linked_notes, [])

    def test_link_nonexistent_note_still_links(self):
        # The task needs source_line to be persisted
        from src.tm_journal import add_task_to_file
        add_task_to_file(self.journal_path, "Test", state="TODO")
        from src.tm_journal import parse_journal
        tasks = parse_journal(self.journal_path)
        task = list(tasks.values())[0][0]
        self.assertTrue(link_note_to_task(self.journal_path, task, "no_exist.md"))

    def test_get_linked_notes_content(self):
        write_note(self.journal_path, "exists.md", "hello")
        task = Task(title="Test", task_id="1", linked_notes=["exists.md", "missing.md"])
        task.source_line = None  # Not needed for get_linked_notes_content
        contents = get_linked_notes_content(self.journal_path, task)
        self.assertEqual(len(contents), 2)
        self.assertTrue(contents[0]["exists"])
        self.assertEqual(contents[0]["content"], "hello")
        self.assertFalse(contents[1]["exists"])
        self.assertIsNone(contents[1]["content"])

    def test_get_linked_notes_empty(self):
        task = Task(title="Test", task_id="1")
        self.assertEqual(get_linked_notes_content(self.journal_path, task), [])

    def test_link_is_idempotent(self):
        task = self._first_task()
        task.linked_notes = ["n.md"]
        self.assertTrue(link_note_to_task(self.journal_path, task, "n.md"))
        self.assertEqual(task.linked_notes, ["n.md"])

    def test_unlink_nonexistent_is_noop(self):
        task = self._first_task()
        task.linked_notes = ["a.md"]
        self.assertTrue(unlink_note_from_task(self.journal_path, task, "b.md"))
        self.assertEqual(task.linked_notes, ["a.md"])


if __name__ == "__main__":
    unittest.main()
