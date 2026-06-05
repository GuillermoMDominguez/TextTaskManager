"""Comprehensive tests for tm_features.py extended features."""

import sys
import json
import csv
import io
import unittest
from datetime import datetime, timedelta
from collections import OrderedDict


from src.tm_features import (
    parse_time_spent, format_time_spent, extract_time_spent_from_line,
    update_time_in_line, get_total_time_spent,
    parse_recurrence, compute_next_recurrence_date, generate_recurring_task_line,
    sort_tasks, get_tasks_by_tag, get_all_tags,
    export_to_json, export_to_csv, import_from_json,
    extract_blockers_from_line, extract_blocks_from_line,
    add_blocker_metadata, add_blocks_metadata,
    remove_blocker_metadata, remove_all_blocker_metadata,
    remove_blocks_metadata, remove_all_blocks_metadata,
    find_task_by_title_match, is_task_blocked,
    extract_subtask_due_date, subtask_due_display,
)
from src.tm_models import Task, Subtask


# ─── Time Tracking Tests ──────────────────────────────────────────────────


class TestParseTimeSpent(unittest.TestCase):
    """Tests for parse_time_spent function."""

    def test_hours_only(self):
        self.assertEqual(parse_time_spent("2h"), 120)

    def test_minutes_only(self):
        self.assertEqual(parse_time_spent("30m"), 30)

    def test_hours_and_minutes(self):
        self.assertEqual(parse_time_spent("1h30m"), 90)

    def test_zero_hours(self):
        self.assertEqual(parse_time_spent("0h"), 0)

    def test_zero_minutes(self):
        self.assertEqual(parse_time_spent("0m"), 0)

    def test_large_hours(self):
        self.assertEqual(parse_time_spent("10h"), 600)

    def test_large_minutes(self):
        self.assertEqual(parse_time_spent("120m"), 120)

    def test_combined_large(self):
        self.assertEqual(parse_time_spent("3h45m"), 225)

    def test_invalid_empty(self):
        self.assertIsNone(parse_time_spent(""))

    def test_invalid_text(self):
        self.assertIsNone(parse_time_spent("abc"))

    def test_invalid_no_unit(self):
        self.assertIsNone(parse_time_spent("30"))

    def test_invalid_wrong_format(self):
        self.assertIsNone(parse_time_spent("h30m"))

    def test_whitespace_handling(self):
        self.assertEqual(parse_time_spent("  2h  "), 120)

    def test_uppercase(self):
        # The function lowercases input
        self.assertEqual(parse_time_spent("2H"), 120)

    def test_mixed_case(self):
        self.assertEqual(parse_time_spent("1H30M"), 90)


class TestFormatTimeSpent(unittest.TestCase):
    """Tests for format_time_spent function."""

    def test_zero(self):
        self.assertEqual(format_time_spent(0), "0m")

    def test_negative(self):
        self.assertEqual(format_time_spent(-5), "0m")

    def test_minutes_only(self):
        self.assertEqual(format_time_spent(45), "45m")

    def test_exact_hour(self):
        self.assertEqual(format_time_spent(60), "1h")

    def test_hours_and_minutes(self):
        self.assertEqual(format_time_spent(90), "1h30m")

    def test_multiple_hours(self):
        self.assertEqual(format_time_spent(150), "2h30m")

    def test_large_value(self):
        self.assertEqual(format_time_spent(600), "10h")

    def test_one_minute(self):
        self.assertEqual(format_time_spent(1), "1m")


class TestExtractTimeSpentFromLine(unittest.TestCase):
    """Tests for extract_time_spent_from_line function."""

    def test_spent_colon_format(self):
        line = "- Task title -- BACKLOG -- spent:2h30m"
        self.assertEqual(extract_time_spent_from_line(line), 150)

    def test_time_colon_format(self):
        line = "- Task title -- BACKLOG -- time:1h"
        self.assertEqual(extract_time_spent_from_line(line), 60)

    def test_spent_equals_format(self):
        line = "- Task title -- BACKLOG -- spent=45m"
        self.assertEqual(extract_time_spent_from_line(line), 45)

    def test_no_time_metadata(self):
        line = "- Task title -- BACKLOG"
        self.assertIsNone(extract_time_spent_from_line(line))

    def test_case_insensitive(self):
        line = "- Task title -- BACKLOG -- SPENT:2h"
        self.assertEqual(extract_time_spent_from_line(line), 120)

    def test_with_other_metadata(self):
        line = "- Task title -- DONE -- priority:HIGH -- spent:30m"
        self.assertEqual(extract_time_spent_from_line(line), 30)


class TestUpdateTimeInLine(unittest.TestCase):
    """Tests for update_time_in_line function."""

    def test_append_when_no_existing(self):
        line = "- Task title -- BACKLOG"
        result = update_time_in_line(line, 90)
        self.assertIn("-- spent:1h30m", result)

    def test_replace_existing(self):
        line = "- Task title -- BACKLOG -- spent:30m"
        result = update_time_in_line(line, 120)
        self.assertIn("-- spent:2h", result)
        self.assertNotIn("30m", result)

    def test_replace_time_format(self):
        line = "- Task title -- BACKLOG -- time:1h"
        result = update_time_in_line(line, 45)
        self.assertIn("-- spent:45m", result)

    def test_zero_minutes(self):
        line = "- Task title -- BACKLOG"
        result = update_time_in_line(line, 0)
        self.assertIn("-- spent:0m", result)


class TestGetTotalTimeSpent(unittest.TestCase):
    """Tests for get_total_time_spent function."""

    def test_single_task_with_time(self):
        task = Task(title="Task 1", time_spent=60)
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        self.assertEqual(get_total_time_spent(tasks_by_date), 60)

    def test_multiple_tasks(self):
        t1 = Task(title="Task 1", time_spent=30)
        t2 = Task(title="Task 2", time_spent=45)
        tasks_by_date = {datetime(2026, 1, 1): [t1, t2]}
        self.assertEqual(get_total_time_spent(tasks_by_date), 75)

    def test_tasks_without_time(self):
        task = Task(title="Task 1", time_spent=None)
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        self.assertEqual(get_total_time_spent(tasks_by_date), 0)

    def test_mixed_tasks(self):
        t1 = Task(title="Task 1", time_spent=60)
        t2 = Task(title="Task 2", time_spent=None)
        t3 = Task(title="Task 3", time_spent=30)
        tasks_by_date = {datetime(2026, 1, 1): [t1, t2, t3]}
        self.assertEqual(get_total_time_spent(tasks_by_date), 90)

    def test_empty_dict(self):
        self.assertEqual(get_total_time_spent({}), 0)

    def test_multiple_dates(self):
        t1 = Task(title="Task 1", time_spent=60)
        t2 = Task(title="Task 2", time_spent=45)
        tasks_by_date = {
            datetime(2026, 1, 1): [t1],
            datetime(2026, 1, 2): [t2],
        }
        self.assertEqual(get_total_time_spent(tasks_by_date), 105)


# ─── Recurrence Tests ─────────────────────────────────────────────────────


class TestParseRecurrence(unittest.TestCase):
    """Tests for parse_recurrence function."""

    def test_recur_colon_daily(self):
        self.assertEqual(parse_recurrence("recur:daily"), "daily")

    def test_recur_colon_weekly(self):
        self.assertEqual(parse_recurrence("recur:weekly"), "weekly")

    def test_recurrence_equals_monthly(self):
        self.assertEqual(parse_recurrence("recurrence=monthly"), "monthly")

    def test_recur_biweekly(self):
        self.assertEqual(parse_recurrence("recur:biweekly"), "biweekly")

    def test_recur_yearly(self):
        self.assertEqual(parse_recurrence("recur:yearly"), "yearly")

    def test_case_insensitive(self):
        self.assertEqual(parse_recurrence("RECUR:WEEKLY"), "weekly")

    def test_recurrence_with_space(self):
        self.assertEqual(parse_recurrence("recur: daily"), "daily")

    def test_invalid_recurrence_value(self):
        self.assertIsNone(parse_recurrence("recur:hourly"))

    def test_no_recurrence(self):
        self.assertIsNone(parse_recurrence("some random text"))

    def test_empty_string(self):
        self.assertIsNone(parse_recurrence(""))

    def test_embedded_in_line(self):
        self.assertEqual(
            parse_recurrence("- Task -- BACKLOG -- recur:weekly"),
            "weekly",
        )


class TestComputeNextRecurrenceDate(unittest.TestCase):
    """Tests for compute_next_recurrence_date function."""

    def test_daily(self):
        date = datetime(2026, 6, 1)
        result = compute_next_recurrence_date(date, "daily")
        self.assertEqual(result, datetime(2026, 6, 2))

    def test_weekly(self):
        date = datetime(2026, 6, 1)
        result = compute_next_recurrence_date(date, "weekly")
        self.assertEqual(result, datetime(2026, 6, 8))

    def test_biweekly(self):
        date = datetime(2026, 6, 1)
        result = compute_next_recurrence_date(date, "biweekly")
        self.assertEqual(result, datetime(2026, 6, 15))

    def test_monthly_standard(self):
        date = datetime(2026, 1, 15)
        result = compute_next_recurrence_date(date, "monthly")
        self.assertEqual(result, datetime(2026, 2, 15))

    def test_monthly_jan31_to_feb28(self):
        date = datetime(2026, 1, 31)
        result = compute_next_recurrence_date(date, "monthly")
        self.assertEqual(result, datetime(2026, 2, 28))

    def test_monthly_december_to_january(self):
        date = datetime(2026, 12, 15)
        result = compute_next_recurrence_date(date, "monthly")
        self.assertEqual(result, datetime(2027, 1, 15))

    def test_monthly_march31_to_april30(self):
        date = datetime(2026, 3, 31)
        result = compute_next_recurrence_date(date, "monthly")
        self.assertEqual(result, datetime(2026, 4, 30))

    def test_yearly_standard(self):
        date = datetime(2026, 6, 5)
        result = compute_next_recurrence_date(date, "yearly")
        self.assertEqual(result, datetime(2027, 6, 5))

    def test_yearly_leap_day(self):
        # Feb 29 in a leap year -> Feb 28 in non-leap year
        date = datetime(2024, 2, 29)
        result = compute_next_recurrence_date(date, "yearly")
        self.assertEqual(result, datetime(2025, 2, 28))

    def test_unknown_falls_back_to_weekly(self):
        date = datetime(2026, 6, 1)
        result = compute_next_recurrence_date(date, "unknown")
        self.assertEqual(result, datetime(2026, 6, 8))


class TestGenerateRecurringTaskLine(unittest.TestCase):
    """Tests for generate_recurring_task_line function."""

    def test_returns_title_and_date(self):
        task = Task(title="Daily standup")
        next_date = datetime(2026, 6, 10)
        title, date = generate_recurring_task_line(task, "daily", next_date)
        self.assertEqual(title, "Daily standup")
        self.assertEqual(date, next_date)

    def test_preserves_original_title(self):
        task = Task(title="Review PRs #code")
        next_date = datetime(2026, 7, 1)
        title, date = generate_recurring_task_line(task, "weekly", next_date)
        self.assertEqual(title, "Review PRs #code")


# ─── Sorting Tests ─────────────────────────────────────────────────────────


class TestSortTasks(unittest.TestCase):
    """Tests for sort_tasks function."""

    def setUp(self):
        self.tasks = [
            Task(title="Low", priority="LOW"),
            Task(title="Urgent", priority="URGENT"),
            Task(title="Medium", priority="MEDIUM"),
            Task(title="High", priority="HIGH"),
        ]

    def test_sort_none_returns_unchanged(self):
        original = list(self.tasks)
        result = sort_tasks(self.tasks, "none")
        self.assertEqual(result, original)

    def test_sort_priority_asc(self):
        result = sort_tasks(self.tasks, "priority", "asc")
        priorities = [t.priority for t in result]
        self.assertEqual(priorities, ["URGENT", "HIGH", "MEDIUM", "LOW"])

    def test_sort_priority_desc(self):
        result = sort_tasks(self.tasks, "priority", "desc")
        priorities = [t.priority for t in result]
        self.assertEqual(priorities, ["LOW", "MEDIUM", "HIGH", "URGENT"])

    def test_sort_priority_missing_defaults_to_low(self):
        tasks = [
            Task(title="No priority"),
            Task(title="Urgent", priority="URGENT"),
        ]
        result = sort_tasks(tasks, "priority", "asc")
        self.assertEqual(result[0].title, "Urgent")
        self.assertEqual(result[1].title, "No priority")

    def test_sort_due_date_asc(self):
        tasks = [
            Task(title="Later", due_date=datetime(2026, 12, 1)),
            Task(title="Sooner", due_date=datetime(2026, 6, 1)),
        ]
        result = sort_tasks(tasks, "due_date", "asc")
        self.assertEqual(result[0].title, "Sooner")
        self.assertEqual(result[1].title, "Later")

    def test_sort_due_date_none_goes_to_end_asc(self):
        tasks = [
            Task(title="No date"),
            Task(title="Has date", due_date=datetime(2026, 6, 1)),
        ]
        result = sort_tasks(tasks, "due_date", "asc")
        self.assertEqual(result[0].title, "Has date")
        self.assertEqual(result[1].title, "No date")

    def test_sort_due_date_desc(self):
        tasks = [
            Task(title="Sooner", due_date=datetime(2026, 6, 1)),
            Task(title="Later", due_date=datetime(2026, 12, 1)),
        ]
        result = sort_tasks(tasks, "due_date", "desc")
        self.assertEqual(result[0].title, "Later")
        self.assertEqual(result[1].title, "Sooner")

    def test_sort_state(self):
        tasks = [
            Task(title="Done", state="DONE"),
            Task(title="Backlog", state="BACKLOG"),
            Task(title="In Progress", state="IN PROGRESS"),
        ]
        result = sort_tasks(tasks, "state", "asc")
        self.assertEqual(result[0].title, "Backlog")
        self.assertEqual(result[1].title, "In Progress")

    def test_sort_empty_list(self):
        result = sort_tasks([], "priority")
        self.assertEqual(result, [])

    def test_sort_unknown_criterion_returns_unchanged(self):
        original = list(self.tasks)
        result = sort_tasks(self.tasks, "unknown_field")
        self.assertEqual(result, original)


# ─── Tags Tests ────────────────────────────────────────────────────────────


class TestGetTasksByTag(unittest.TestCase):
    """Tests for get_tasks_by_tag function."""

    def test_find_by_tag_in_title(self):
        task = Task(title="Fix #frontend bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_tasks_by_tag(tasks_by_date, "frontend")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Fix #frontend bug")

    def test_tag_with_hash_prefix(self):
        task = Task(title="Fix #frontend bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_tasks_by_tag(tasks_by_date, "#frontend")
        self.assertEqual(len(result), 1)

    def test_case_insensitive(self):
        task = Task(title="Fix #Frontend bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_tasks_by_tag(tasks_by_date, "FRONTEND")
        self.assertEqual(len(result), 1)

    def test_no_match(self):
        task = Task(title="Fix bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_tasks_by_tag(tasks_by_date, "backend")
        self.assertEqual(len(result), 0)

    def test_tag_in_subtask(self):
        subtask = Subtask(title="Style #css component")
        task = Task(title="UI work", subtasks=[subtask])
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_tasks_by_tag(tasks_by_date, "css")
        self.assertEqual(len(result), 1)

    def test_multiple_dates(self):
        t1 = Task(title="Task #api")
        t2 = Task(title="Task #api v2")
        tasks_by_date = {
            datetime(2026, 1, 1): [t1],
            datetime(2026, 1, 2): [t2],
        }
        result = get_tasks_by_tag(tasks_by_date, "api")
        self.assertEqual(len(result), 2)


class TestGetAllTags(unittest.TestCase):
    """Tests for get_all_tags function."""

    def test_single_tag(self):
        task = Task(title="Fix #frontend bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_all_tags(tasks_by_date)
        self.assertEqual(result, {"frontend": 1})

    def test_multiple_tags(self):
        task = Task(title="Fix #frontend #urgent bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_all_tags(tasks_by_date)
        self.assertIn("frontend", result)
        self.assertIn("urgent", result)

    def test_tag_counting(self):
        t1 = Task(title="Fix #api")
        t2 = Task(title="Update #api docs")
        tasks_by_date = {datetime(2026, 1, 1): [t1, t2]}
        result = get_all_tags(tasks_by_date)
        self.assertEqual(result["api"], 2)

    def test_subtask_tags_counted(self):
        subtask = Subtask(title="Review #backend code")
        task = Task(title="Sprint work", subtasks=[subtask])
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_all_tags(tasks_by_date)
        self.assertEqual(result["backend"], 1)

    def test_empty_tasks(self):
        tasks_by_date = {datetime(2026, 1, 1): []}
        result = get_all_tags(tasks_by_date)
        self.assertEqual(result, {})

    def test_no_tags(self):
        task = Task(title="Plain task without tags")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = get_all_tags(tasks_by_date)
        self.assertEqual(result, {})


# ─── Export/Import Tests ───────────────────────────────────────────────────


class TestExportToJson(unittest.TestCase):
    """Tests for export_to_json function."""

    def test_basic_export(self):
        task = Task(title="Test task", state="BACKLOG")
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_json(tasks_by_date)
        data = json.loads(result)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "Test task")
        self.assertEqual(data[0]["state"], "BACKLOG")

    def test_date_format(self):
        task = Task(title="Task", state="DONE")
        tasks_by_date = {datetime(2026, 6, 5): [task]}
        result = export_to_json(tasks_by_date)
        data = json.loads(result)
        self.assertEqual(data[0]["date"], "05/06/2026")

    def test_due_date_included(self):
        task = Task(title="Task", due_date=datetime(2026, 12, 25))
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_json(tasks_by_date)
        data = json.loads(result)
        self.assertEqual(data[0]["due_date"], "25/12/2026")

    def test_subtasks_included(self):
        subtask = Subtask(title="Sub #code", state="IN PROGRESS")
        task = Task(title="Parent", subtasks=[subtask])
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_json(tasks_by_date)
        data = json.loads(result)
        self.assertEqual(len(data[0]["subtasks"]), 1)
        self.assertEqual(data[0]["subtasks"][0]["title"], "Sub #code")
        self.assertEqual(data[0]["subtasks"][0]["state"], "IN PROGRESS")

    def test_tags_extracted(self):
        task = Task(title="Fix #frontend #urgent")
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_json(tasks_by_date)
        data = json.loads(result)
        self.assertIn("frontend", data[0]["tags"])
        self.assertIn("urgent", data[0]["tags"])

    def test_notes_included(self):
        task = Task(title="Task", comments=["Note 1", "Note 2"])
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_json(tasks_by_date)
        data = json.loads(result)
        self.assertEqual(data[0]["notes"], ["Note 1", "Note 2"])

    def test_empty_tasks(self):
        result = export_to_json({})
        data = json.loads(result)
        self.assertEqual(data, [])

    def test_valid_json_output(self):
        task = Task(title="Task with \"quotes\"", state="BACKLOG")
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_json(tasks_by_date)
        # Should not raise
        data = json.loads(result)
        self.assertEqual(data[0]["title"], "Task with \"quotes\"")


class TestExportToCsv(unittest.TestCase):
    """Tests for export_to_csv function."""

    def test_header_row(self):
        tasks_by_date = {datetime(2026, 6, 1): []}
        result = export_to_csv(tasks_by_date)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        self.assertIn("Title", header)
        self.assertIn("State", header)
        self.assertIn("Priority", header)

    def test_task_row(self):
        task = Task(title="CSV task", state="DONE", priority="HIGH")
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_csv(tasks_by_date)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        row = next(reader)
        self.assertIn("CSV task", row)
        self.assertIn("DONE", row)
        self.assertIn("HIGH", row)

    def test_valid_csv_format(self):
        task = Task(title="Task with, comma", state="BACKLOG")
        tasks_by_date = {datetime(2026, 6, 1): [task]}
        result = export_to_csv(tasks_by_date)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        row = next(reader)
        self.assertIn("Task with, comma", row)

    def test_empty_export(self):
        result = export_to_csv({})
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        self.assertTrue(len(header) > 0)
        rows = list(reader)
        self.assertEqual(len(rows), 0)


class TestImportFromJson(unittest.TestCase):
    """Tests for import_from_json function."""

    def test_basic_import(self):
        data = [{"title": "Imported task", "state": "BACKLOG", "date": "01/06/2026"}]
        result = import_from_json(json.dumps(data))
        self.assertTrue(len(result) > 0)
        # Check date header
        self.assertIn("01/06/2026", result[0])
        # Check task line
        task_lines = [l for l in result if "Imported task" in l]
        self.assertTrue(len(task_lines) > 0)

    def test_invalid_json_returns_empty(self):
        result = import_from_json("not valid json{{{")
        self.assertEqual(result, [])

    def test_empty_json_array(self):
        result = import_from_json("[]")
        self.assertEqual(result, [])

    def test_subtasks_imported(self):
        data = [{
            "title": "Parent",
            "state": "BACKLOG",
            "date": "01/06/2026",
            "subtasks": [{"title": "Child", "state": "IN PROGRESS"}],
        }]
        result = import_from_json(json.dumps(data))
        subtask_lines = [l for l in result if "Child" in l]
        self.assertTrue(len(subtask_lines) > 0)
        self.assertIn("+", subtask_lines[0])

    def test_notes_imported(self):
        data = [{
            "title": "Task",
            "state": "BACKLOG",
            "date": "01/06/2026",
            "notes": ["Important note"],
        }]
        result = import_from_json(json.dumps(data))
        note_lines = [l for l in result if "Important note" in l]
        self.assertTrue(len(note_lines) > 0)
        self.assertTrue(note_lines[0].startswith(":"))

    def test_due_date_preserved(self):
        data = [{
            "title": "Task",
            "state": "BACKLOG",
            "date": "01/06/2026",
            "due_date": "15/06/2026",
        }]
        result = import_from_json(json.dumps(data))
        task_lines = [l for l in result if "Task" in l and "due:" in l]
        self.assertTrue(len(task_lines) > 0)

    def test_priority_preserved(self):
        data = [{
            "title": "Task",
            "state": "BACKLOG",
            "date": "01/06/2026",
            "priority": "HIGH",
        }]
        result = import_from_json(json.dumps(data))
        task_lines = [l for l in result if "priority:HIGH" in l]
        self.assertTrue(len(task_lines) > 0)

    def test_groups_by_date(self):
        data = [
            {"title": "Task 1", "state": "BACKLOG", "date": "01/06/2026"},
            {"title": "Task 2", "state": "BACKLOG", "date": "02/06/2026"},
        ]
        result = import_from_json(json.dumps(data))
        date_headers = [l for l in result if l.startswith("## ")]
        self.assertEqual(len(date_headers), 2)


# ─── Blockers Tests ────────────────────────────────────────────────────────


class TestExtractBlockersFromLine(unittest.TestCase):
    """Tests for extract_blockers_from_line function."""

    def test_single_blocker(self):
        line = "- Task -- BACKLOG -- blockedby:Setup database"
        result = extract_blockers_from_line(line)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].strip(), "Setup database")

    def test_no_blockers(self):
        line = "- Task -- BACKLOG"
        result = extract_blockers_from_line(line)
        self.assertEqual(result, [])

    def test_blockedby_equals_format(self):
        line = "- Task -- BACKLOG -- blockedby=Other task"
        result = extract_blockers_from_line(line)
        self.assertEqual(len(result), 1)

    def test_case_insensitive(self):
        line = "- Task -- BACKLOG -- BLOCKEDBY:Other"
        result = extract_blockers_from_line(line)
        self.assertEqual(len(result), 1)


class TestExtractBlocksFromLine(unittest.TestCase):
    """Tests for extract_blocks_from_line function."""

    def test_single_blocks(self):
        line = "- Setup DB -- DONE -- blocks:Deploy app"
        result = extract_blocks_from_line(line)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].strip(), "Deploy app")

    def test_no_blocks(self):
        line = "- Task -- BACKLOG"
        result = extract_blocks_from_line(line)
        self.assertEqual(result, [])

    def test_case_insensitive(self):
        line = "- Task -- BACKLOG -- BLOCKS:Other task"
        result = extract_blocks_from_line(line)
        self.assertEqual(len(result), 1)


class TestAddBlockerMetadata(unittest.TestCase):
    """Tests for add_blocker_metadata function."""

    def test_appends_blocker(self):
        line = "- Task -- BACKLOG"
        result = add_blocker_metadata(line, "Setup database")
        self.assertIn("-- blockedby:Setup database", result)

    def test_strips_blocker_title(self):
        line = "- Task -- BACKLOG"
        result = add_blocker_metadata(line, "  Blocker  ")
        self.assertIn("-- blockedby:Blocker", result)

    def test_preserves_existing_content(self):
        line = "- Task -- BACKLOG -- priority:HIGH"
        result = add_blocker_metadata(line, "Other")
        self.assertIn("priority:HIGH", result)
        self.assertIn("blockedby:Other", result)


class TestAddBlocksMetadata(unittest.TestCase):
    """Tests for add_blocks_metadata function."""

    def test_appends_blocks(self):
        line = "- Setup -- DONE"
        result = add_blocks_metadata(line, "Deploy app")
        self.assertIn("-- blocks:Deploy app", result)

    def test_strips_blocked_title(self):
        line = "- Task -- DONE"
        result = add_blocks_metadata(line, "  Blocked  ")
        self.assertIn("-- blocks:Blocked", result)


class TestRemoveBlockerMetadata(unittest.TestCase):
    """Tests for remove_blocker_metadata function."""

    def test_removes_specific_blocker(self):
        line = "- Task -- BACKLOG -- blockedby:Setup DB"
        result = remove_blocker_metadata(line, "Setup DB")
        self.assertNotIn("blockedby", result)

    def test_preserves_other_metadata(self):
        line = "- Task -- BACKLOG -- priority:HIGH -- blockedby:Other"
        result = remove_blocker_metadata(line, "Other")
        self.assertIn("priority:HIGH", result)
        self.assertNotIn("blockedby", result)


class TestRemoveAllBlockerMetadata(unittest.TestCase):
    """Tests for remove_all_blocker_metadata function."""

    def test_removes_all_blockers(self):
        line = "- Task -- BACKLOG -- blockedby:Task A -- blockedby:Task B"
        result = remove_all_blocker_metadata(line)
        self.assertNotIn("blockedby", result)

    def test_preserves_non_blocker_metadata(self):
        line = "- Task -- BACKLOG -- priority:HIGH -- blockedby:Other"
        result = remove_all_blocker_metadata(line)
        self.assertIn("priority:HIGH", result)


class TestRemoveBlocksMetadata(unittest.TestCase):
    """Tests for remove_blocks_metadata function."""

    def test_removes_specific_blocks(self):
        line = "- Setup -- DONE -- blocks:Deploy"
        result = remove_blocks_metadata(line, "Deploy")
        self.assertNotIn("blocks", result)

    def test_preserves_other_metadata(self):
        line = "- Setup -- DONE -- priority:HIGH -- blocks:Deploy"
        result = remove_blocks_metadata(line, "Deploy")
        self.assertIn("priority:HIGH", result)


class TestRemoveAllBlocksMetadata(unittest.TestCase):
    """Tests for remove_all_blocks_metadata function."""

    def test_removes_all_blocks(self):
        line = "- Task -- DONE -- blocks:A -- blocks:B"
        result = remove_all_blocks_metadata(line)
        self.assertNotIn("blocks", result)


class TestFindTaskByTitleMatch(unittest.TestCase):
    """Tests for find_task_by_title_match function."""

    def test_exact_match(self):
        task = Task(title="Fix login bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = find_task_by_title_match(tasks_by_date, "Fix login bug")
        self.assertEqual(result, task)

    def test_case_insensitive(self):
        task = Task(title="Fix Login Bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = find_task_by_title_match(tasks_by_date, "fix login bug")
        self.assertEqual(result, task)

    def test_strips_whitespace(self):
        task = Task(title="Fix bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = find_task_by_title_match(tasks_by_date, "  Fix bug  ")
        self.assertEqual(result, task)

    def test_not_found(self):
        task = Task(title="Fix bug")
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        result = find_task_by_title_match(tasks_by_date, "Nonexistent")
        self.assertIsNone(result)

    def test_multiple_dates(self):
        t1 = Task(title="Task A")
        t2 = Task(title="Task B")
        tasks_by_date = {
            datetime(2026, 1, 1): [t1],
            datetime(2026, 1, 2): [t2],
        }
        result = find_task_by_title_match(tasks_by_date, "Task B")
        self.assertEqual(result, t2)


class TestIsTaskBlocked(unittest.TestCase):
    """Tests for is_task_blocked function."""

    def test_blocked_by_unfinished_task(self):
        blocker = Task(title="Setup DB", state="IN PROGRESS")
        task = Task(title="Deploy", blocked_by=["Setup DB"])
        tasks_by_date = {datetime(2026, 1, 1): [blocker, task]}
        self.assertTrue(is_task_blocked(task, tasks_by_date))

    def test_not_blocked_when_blocker_done(self):
        blocker = Task(title="Setup DB", state="DONE")
        task = Task(title="Deploy", blocked_by=["Setup DB"])
        tasks_by_date = {datetime(2026, 1, 1): [blocker, task]}
        self.assertFalse(is_task_blocked(task, tasks_by_date))

    def test_not_blocked_when_no_blockers(self):
        task = Task(title="Deploy", blocked_by=[])
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        self.assertFalse(is_task_blocked(task, tasks_by_date))

    def test_not_blocked_when_blocker_not_found(self):
        task = Task(title="Deploy", blocked_by=["Nonexistent task"])
        tasks_by_date = {datetime(2026, 1, 1): [task]}
        self.assertFalse(is_task_blocked(task, tasks_by_date))

    def test_blocked_by_cancelled_is_not_blocked(self):
        blocker = Task(title="Setup DB", state="CANCELLED")
        task = Task(title="Deploy", blocked_by=["Setup DB"])
        tasks_by_date = {datetime(2026, 1, 1): [blocker, task]}
        self.assertFalse(is_task_blocked(task, tasks_by_date))


# ─── Subtask Due Date Tests ────────────────────────────────────────────────


class TestExtractSubtaskDueDate(unittest.TestCase):
    """Tests for extract_subtask_due_date function."""

    def test_valid_due_date(self):
        result = extract_subtask_due_date("Complete review [due=10/06/2026]")
        self.assertEqual(result, datetime(2026, 6, 10))

    def test_no_due_date(self):
        result = extract_subtask_due_date("Simple subtask")
        self.assertIsNone(result)

    def test_due_at_start(self):
        result = extract_subtask_due_date("[due=01/01/2027] Start project")
        self.assertEqual(result, datetime(2027, 1, 1))

    def test_invalid_date_format(self):
        result = extract_subtask_due_date("Task [due=invalid]")
        self.assertIsNone(result)

    def test_single_digit_day_month(self):
        result = extract_subtask_due_date("Task [due=5/6/2026]")
        self.assertEqual(result, datetime(2026, 6, 5))

    def test_case_insensitive_due(self):
        result = extract_subtask_due_date("Task [DUE=10/06/2026]")
        self.assertEqual(result, datetime(2026, 6, 10))


class TestSubtaskDueDisplay(unittest.TestCase):
    """Tests for subtask_due_display function."""

    def test_with_due_date(self):
        subtask = Subtask(title="Review [due=10/06/2026]")
        result = subtask_due_display(subtask)
        self.assertEqual(result, "10/06/2026")

    def test_without_due_date(self):
        subtask = Subtask(title="Simple subtask")
        result = subtask_due_display(subtask)
        self.assertIsNone(result)

    def test_formatted_output(self):
        subtask = Subtask(title="Deliver [due=25/12/2026]")
        result = subtask_due_display(subtask)
        self.assertEqual(result, "25/12/2026")


if __name__ == "__main__":
    unittest.main()
