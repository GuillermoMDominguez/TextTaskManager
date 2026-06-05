"""Comprehensive tests for tm_models and tm_logic modules."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tm_models import Task, Subtask, extract_tags_from_text, TAG_PATTERN
from tm_logic import (
    normalize_state_input,
    normalize_priority_input,
    parse_date_input,
    normalize_task_id_input,
    assign_task_ids,
    find_task_by_id,
    normalize_note_id_input,
    build_note_id,
    find_note_by_id,
    task_matches_search,
    normalize_recurrence_input,
    parse_new_command_args,
    get_pending_tasks,
    get_id_width,
)


# ===========================================================================
# Tests for tm_models.extract_tags_from_text
# ===========================================================================

class TestExtractTagsFromText(unittest.TestCase):
    """Tests for extract_tags_from_text function."""

    def test_single_tag(self):
        self.assertEqual(extract_tags_from_text("Hello #world"), ["world"])

    def test_multiple_tags(self):
        self.assertEqual(extract_tags_from_text("#foo and #bar"), ["foo", "bar"])

    def test_tags_lowercased(self):
        self.assertEqual(extract_tags_from_text("#FOO #Bar #baz"), ["foo", "bar", "baz"])

    def test_duplicate_tags_deduplicated(self):
        self.assertEqual(extract_tags_from_text("#foo #FOO #Foo"), ["foo"])

    def test_tag_with_hyphens(self):
        self.assertEqual(extract_tags_from_text("#my-tag"), ["my-tag"])

    def test_tag_with_underscores(self):
        self.assertEqual(extract_tags_from_text("#my_tag"), ["my_tag"])

    def test_tag_with_numbers(self):
        self.assertEqual(extract_tags_from_text("#v2 #item3"), ["v2", "item3"])

    def test_no_tags(self):
        self.assertEqual(extract_tags_from_text("no tags here"), [])

    def test_empty_string(self):
        self.assertEqual(extract_tags_from_text(""), [])

    def test_none_input(self):
        self.assertEqual(extract_tags_from_text(None), [])

    def test_tag_at_start_of_string(self):
        self.assertEqual(extract_tags_from_text("#first word"), ["first"])

    def test_tag_at_end_of_string(self):
        self.assertEqual(extract_tags_from_text("word #last"), ["last"])

    def test_no_tag_after_word_char(self):
        # TAG_PATTERN uses negative lookbehind for word char (\w = [A-Za-z0-9_])
        self.assertEqual(extract_tags_from_text("word#notag"), [])

    def test_tag_after_punctuation(self):
        self.assertEqual(extract_tags_from_text("check.#tag"), ["tag"])

    def test_multiple_hashes_not_tag(self):
        # ##word - first # is not preceded by word char, matches "word" via second #?
        # Actually (?<!\w)#([...]+) will match first # if preceded by non-word
        result = extract_tags_from_text("##double")
        # The first # is not preceded by \w, so it matches. Then the second # is preceded by #, not \w.
        # Actually the pattern captures after #, so ##double -> first match: #double? No.
        # (?<!\w)# matches the first #. Then captures ([A-Za-z0-9_-]+) = but next char is # which is not in charset
        # So first # fails. Second #: preceded by #, # is not \w, so (?<!\w) passes. Captures "double".
        self.assertEqual(result, ["double"])

    def test_preserves_order(self):
        self.assertEqual(extract_tags_from_text("#c #a #b"), ["c", "a", "b"])

    def test_tag_mixed_with_text(self):
        self.assertEqual(
            extract_tags_from_text("Fix #bug in #module-core for #v2"),
            ["bug", "module-core", "v2"],
        )


# ===========================================================================
# Tests for tm_models.Task
# ===========================================================================

class TestTask(unittest.TestCase):
    """Tests for the Task dataclass."""

    def test_default_state(self):
        t = Task(title="test")
        self.assertEqual(t.state, "BACKLOG")

    def test_is_finished_done(self):
        t = Task(title="test", state="DONE")
        self.assertTrue(t.is_finished())

    def test_is_finished_cancelled(self):
        t = Task(title="test", state="CANCELLED")
        self.assertTrue(t.is_finished())

    def test_is_finished_false(self):
        t = Task(title="test", state="BACKLOG")
        self.assertFalse(t.is_finished())

    def test_is_finished_in_progress(self):
        t = Task(title="test", state="IN PROGRESS")
        self.assertFalse(t.is_finished())

    def test_is_in_progress_true(self):
        t = Task(title="test", state="IN PROGRESS")
        self.assertTrue(t.is_in_progress())

    def test_is_in_progress_false(self):
        t = Task(title="test", state="BACKLOG")
        self.assertFalse(t.is_in_progress())

    def test_is_in_testing_testing(self):
        t = Task(title="test", state="TESTING")
        self.assertTrue(t.is_in_testing())

    def test_is_in_testing_in_testing(self):
        t = Task(title="test", state="IN TESTING")
        self.assertTrue(t.is_in_testing())

    def test_is_in_testing_false(self):
        t = Task(title="test", state="DONE")
        self.assertFalse(t.is_in_testing())

    def test_get_tags(self):
        t = Task(title="Fix #bug in #module")
        self.assertEqual(t.get_tags(), ["bug", "module"])

    def test_get_tags_no_tags(self):
        t = Task(title="No tags here")
        self.assertEqual(t.get_tags(), [])

    def test_default_fields(self):
        t = Task(title="test")
        self.assertEqual(t.comments, [])
        self.assertEqual(t.subtasks, [])
        self.assertIsNone(t.date)
        self.assertIsNone(t.due_date)
        self.assertIsNone(t.priority)
        self.assertIsNone(t.recurrence)
        self.assertIsNone(t.time_spent)
        self.assertEqual(t.blocked_by, [])
        self.assertEqual(t.blocks, [])
        self.assertIsNone(t.task_id)
        self.assertIsNone(t.source_line)


# ===========================================================================
# Tests for tm_models.Subtask
# ===========================================================================

class TestSubtask(unittest.TestCase):
    """Tests for the Subtask dataclass."""

    def test_default_state(self):
        s = Subtask(title="sub")
        self.assertEqual(s.state, "BACKLOG")

    def test_is_finished_done(self):
        s = Subtask(title="sub", state="DONE")
        self.assertTrue(s.is_finished())

    def test_is_finished_cancelled(self):
        s = Subtask(title="sub", state="CANCELLED")
        self.assertTrue(s.is_finished())

    def test_is_finished_false(self):
        s = Subtask(title="sub", state="WAITING")
        self.assertFalse(s.is_finished())

    def test_is_in_progress_true(self):
        s = Subtask(title="sub", state="IN PROGRESS")
        self.assertTrue(s.is_in_progress())

    def test_is_in_progress_false(self):
        s = Subtask(title="sub", state="DONE")
        self.assertFalse(s.is_in_progress())

    def test_is_in_testing_true(self):
        s = Subtask(title="sub", state="TESTING")
        self.assertTrue(s.is_in_testing())

    def test_is_in_testing_in_testing(self):
        s = Subtask(title="sub", state="IN TESTING")
        self.assertTrue(s.is_in_testing())

    def test_is_in_testing_false(self):
        s = Subtask(title="sub", state="BACKLOG")
        self.assertFalse(s.is_in_testing())

    def test_get_tags(self):
        s = Subtask(title="Fix #ui component")
        self.assertEqual(s.get_tags(), ["ui"])

    def test_default_fields(self):
        s = Subtask(title="sub")
        self.assertEqual(s.comments, [])
        self.assertIsNone(s.task_id)
        self.assertIsNone(s.source_line)
        self.assertIsNone(s.due_date)
        self.assertIsNone(s.priority)


# ===========================================================================
# Tests for tm_logic.normalize_state_input
# ===========================================================================

class TestNormalizeStateInput(unittest.TestCase):
    """Tests for normalize_state_input function."""

    def test_valid_state_backlog(self):
        self.assertEqual(normalize_state_input("BACKLOG"), "BACKLOG")

    def test_valid_state_in_progress(self):
        self.assertEqual(normalize_state_input("IN PROGRESS"), "IN PROGRESS")

    def test_valid_state_waiting(self):
        self.assertEqual(normalize_state_input("WAITING"), "WAITING")

    def test_valid_state_testing(self):
        self.assertEqual(normalize_state_input("TESTING"), "TESTING")

    def test_valid_state_done(self):
        self.assertEqual(normalize_state_input("DONE"), "DONE")

    def test_valid_state_cancelled(self):
        self.assertEqual(normalize_state_input("CANCELLED"), "CANCELLED")

    def test_lowercase_input(self):
        self.assertEqual(normalize_state_input("backlog"), "BACKLOG")

    def test_mixed_case(self):
        self.assertEqual(normalize_state_input("In Progress"), "IN PROGRESS")

    def test_alias_ip(self):
        self.assertEqual(normalize_state_input("IP"), "IN PROGRESS")

    def test_alias_bl(self):
        self.assertEqual(normalize_state_input("BL"), "BACKLOG")

    def test_alias_wt(self):
        self.assertEqual(normalize_state_input("WT"), "WAITING")

    def test_alias_dn(self):
        self.assertEqual(normalize_state_input("DN"), "DONE")

    def test_alias_cn(self):
        self.assertEqual(normalize_state_input("CN"), "CANCELLED")

    def test_alias_ts(self):
        self.assertEqual(normalize_state_input("TS"), "TESTING")

    def test_alias_in_testing(self):
        self.assertEqual(normalize_state_input("IN TESTING"), "TESTING")

    def test_underscore_replacement(self):
        self.assertEqual(normalize_state_input("in_progress"), "IN PROGRESS")

    def test_with_whitespace(self):
        self.assertEqual(normalize_state_input("  DONE  "), "DONE")

    def test_invalid_state(self):
        self.assertIsNone(normalize_state_input("INVALID"))

    def test_empty_string(self):
        self.assertIsNone(normalize_state_input(""))

    def test_alias_lowercase(self):
        self.assertEqual(normalize_state_input("ip"), "IN PROGRESS")


# ===========================================================================
# Tests for tm_logic.normalize_priority_input
# ===========================================================================

class TestNormalizePriorityInput(unittest.TestCase):
    """Tests for normalize_priority_input function."""

    def test_valid_low(self):
        self.assertEqual(normalize_priority_input("LOW"), "LOW")

    def test_valid_medium(self):
        self.assertEqual(normalize_priority_input("MEDIUM"), "MEDIUM")

    def test_valid_high(self):
        self.assertEqual(normalize_priority_input("HIGH"), "HIGH")

    def test_valid_urgent(self):
        self.assertEqual(normalize_priority_input("URGENT"), "URGENT")

    def test_lowercase(self):
        self.assertEqual(normalize_priority_input("high"), "HIGH")

    def test_alias_l(self):
        self.assertEqual(normalize_priority_input("L"), "LOW")

    def test_alias_m(self):
        self.assertEqual(normalize_priority_input("M"), "MEDIUM")

    def test_alias_h(self):
        self.assertEqual(normalize_priority_input("H"), "HIGH")

    def test_alias_u(self):
        self.assertEqual(normalize_priority_input("U"), "URGENT")

    def test_alias_lowercase(self):
        self.assertEqual(normalize_priority_input("h"), "HIGH")

    def test_with_whitespace(self):
        self.assertEqual(normalize_priority_input("  HIGH  "), "HIGH")

    def test_invalid(self):
        self.assertIsNone(normalize_priority_input("CRITICAL"))

    def test_empty_string(self):
        self.assertIsNone(normalize_priority_input(""))


# ===========================================================================
# Tests for tm_logic.parse_date_input
# ===========================================================================

class TestParseDateInput(unittest.TestCase):
    """Tests for parse_date_input function."""

    def test_dd_mm_yyyy_format(self):
        result = parse_date_input("25/12/2025")
        self.assertEqual(result, datetime(2025, 12, 25))

    def test_dd_mm_yyyy_leading_zeros(self):
        result = parse_date_input("01/01/2025")
        self.assertEqual(result, datetime(2025, 1, 1))

    def test_today(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("today"), today)

    def test_tomorrow(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("tomorrow"), today + timedelta(days=1))

    def test_yesterday(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("yesterday"), today - timedelta(days=1))

    def test_relative_days(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("+3d"), today + timedelta(days=3))

    def test_relative_weeks(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("+2w"), today + timedelta(weeks=2))

    def test_relative_months(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = parse_date_input("+1m")
        self.assertIsNotNone(result)
        # Should be roughly one month ahead
        self.assertGreater(result, today)

    def test_relative_zero_days(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("+0d"), today)

    def test_next_week(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("next week"), today + timedelta(weeks=1))

    def test_nextweek_no_space(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("nextweek"), today + timedelta(weeks=1))

    def test_day_name_monday(self):
        result = parse_date_input("monday")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 0)  # Monday

    def test_day_name_friday(self):
        result = parse_date_input("friday")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 4)  # Friday

    def test_day_abbrev_mon(self):
        result = parse_date_input("mon")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 0)

    def test_day_abbrev_fri(self):
        result = parse_date_input("fri")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 4)

    def test_day_abbrev_sun(self):
        result = parse_date_input("sun")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 6)

    def test_day_name_is_future(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = parse_date_input("tuesday")
        self.assertGreater(result, today)

    def test_invalid_date(self):
        self.assertIsNone(parse_date_input("not-a-date"))

    def test_invalid_format(self):
        self.assertIsNone(parse_date_input("2025-12-25"))

    def test_empty_string(self):
        self.assertIsNone(parse_date_input(""))

    def test_whitespace_trimmed(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("  today  "), today)

    def test_case_insensitive(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(parse_date_input("TODAY"), today)


# ===========================================================================
# Tests for tm_logic.normalize_task_id_input
# ===========================================================================

class TestNormalizeTaskIdInput(unittest.TestCase):
    """Tests for normalize_task_id_input function."""

    def test_simple_id(self):
        self.assertEqual(normalize_task_id_input("1"), "1")

    def test_leading_zeros(self):
        self.assertEqual(normalize_task_id_input("01"), "1")

    def test_multiple_leading_zeros(self):
        self.assertEqual(normalize_task_id_input("007"), "7")

    def test_subtask_id(self):
        self.assertEqual(normalize_task_id_input("1.2"), "1.2")

    def test_subtask_leading_zeros(self):
        self.assertEqual(normalize_task_id_input("01.02"), "1.2")

    def test_subtask_mixed_zeros(self):
        self.assertEqual(normalize_task_id_input("03.1"), "3.1")

    def test_with_whitespace(self):
        self.assertEqual(normalize_task_id_input("  5  "), "5")

    def test_invalid_text(self):
        self.assertIsNone(normalize_task_id_input("abc"))

    def test_invalid_empty(self):
        self.assertIsNone(normalize_task_id_input(""))

    def test_invalid_dots_only(self):
        self.assertIsNone(normalize_task_id_input(".."))

    def test_invalid_triple_segment(self):
        self.assertIsNone(normalize_task_id_input("1.2.3"))

    def test_large_id(self):
        self.assertEqual(normalize_task_id_input("100"), "100")

    def test_large_subtask_id(self):
        self.assertEqual(normalize_task_id_input("99.50"), "99.50")


# ===========================================================================
# Tests for tm_logic.assign_task_ids
# ===========================================================================

class TestAssignTaskIds(unittest.TestCase):
    """Tests for assign_task_ids function."""

    def test_single_task(self):
        tasks_by_date = {None: [Task(title="T1")]}
        assign_task_ids(tasks_by_date)
        self.assertEqual(tasks_by_date[None][0].task_id, "1")

    def test_multiple_tasks(self):
        tasks_by_date = {None: [Task(title="T1"), Task(title="T2"), Task(title="T3")]}
        assign_task_ids(tasks_by_date)
        self.assertEqual(tasks_by_date[None][0].task_id, "1")
        self.assertEqual(tasks_by_date[None][1].task_id, "2")
        self.assertEqual(tasks_by_date[None][2].task_id, "3")

    def test_subtask_ids(self):
        t = Task(title="T1", subtasks=[Subtask(title="S1"), Subtask(title="S2")])
        tasks_by_date = {None: [t]}
        assign_task_ids(tasks_by_date)
        self.assertEqual(t.subtasks[0].task_id, "1.1")
        self.assertEqual(t.subtasks[1].task_id, "1.2")

    def test_multiple_dates(self):
        d1 = datetime(2025, 1, 1)
        d2 = datetime(2025, 1, 2)
        tasks_by_date = {d1: [Task(title="T1")], d2: [Task(title="T2")]}
        assign_task_ids(tasks_by_date)
        ids = [t.task_id for tasks in tasks_by_date.values() for t in tasks]
        self.assertEqual(sorted(ids), ["1", "2"])

    def test_empty_dict(self):
        tasks_by_date = {}
        assign_task_ids(tasks_by_date)  # Should not raise

    def test_empty_task_list(self):
        tasks_by_date = {None: []}
        assign_task_ids(tasks_by_date)  # Should not raise


# ===========================================================================
# Tests for tm_logic.find_task_by_id
# ===========================================================================

class TestFindTaskById(unittest.TestCase):
    """Tests for find_task_by_id function."""

    def setUp(self):
        self.t1 = Task(title="T1", subtasks=[Subtask(title="S1")])
        self.t2 = Task(title="T2")
        self.tasks_by_date = {None: [self.t1, self.t2]}
        assign_task_ids(self.tasks_by_date)

    def test_find_parent_task(self):
        result = find_task_by_id(self.tasks_by_date, "1")
        self.assertIs(result, self.t1)

    def test_find_second_task(self):
        result = find_task_by_id(self.tasks_by_date, "2")
        self.assertIs(result, self.t2)

    def test_find_subtask(self):
        result = find_task_by_id(self.tasks_by_date, "1.1")
        self.assertIs(result, self.t1.subtasks[0])

    def test_find_with_leading_zeros(self):
        result = find_task_by_id(self.tasks_by_date, "01")
        self.assertIs(result, self.t1)

    def test_find_nonexistent(self):
        self.assertIsNone(find_task_by_id(self.tasks_by_date, "99"))

    def test_find_invalid_id(self):
        self.assertIsNone(find_task_by_id(self.tasks_by_date, "abc"))


# ===========================================================================
# Tests for tm_logic.normalize_note_id_input
# ===========================================================================

class TestNormalizeNoteIdInput(unittest.TestCase):
    """Tests for normalize_note_id_input function."""

    def test_basic(self):
        self.assertEqual(normalize_note_id_input("1:n1"), ("1", 1))

    def test_leading_zeros(self):
        self.assertEqual(normalize_note_id_input("01:n02"), ("1", 2))

    def test_large_numbers(self):
        self.assertEqual(normalize_note_id_input("15:n3"), ("15", 3))

    def test_with_whitespace(self):
        self.assertEqual(normalize_note_id_input("  2:n1  "), ("2", 1))

    def test_case_insensitive(self):
        self.assertEqual(normalize_note_id_input("1:N1"), ("1", 1))

    def test_invalid_no_n(self):
        self.assertIsNone(normalize_note_id_input("1:1"))

    def test_invalid_no_colon(self):
        self.assertIsNone(normalize_note_id_input("1n1"))

    def test_invalid_empty(self):
        self.assertIsNone(normalize_note_id_input(""))

    def test_invalid_text(self):
        self.assertIsNone(normalize_note_id_input("abc"))


# ===========================================================================
# Tests for tm_logic.build_note_id
# ===========================================================================

class TestBuildNoteId(unittest.TestCase):
    """Tests for build_note_id function."""

    def test_basic(self):
        self.assertEqual(build_note_id("1", 1), "1:n1")

    def test_larger_numbers(self):
        self.assertEqual(build_note_id("10", 5), "10:n5")

    def test_subtask_id(self):
        self.assertEqual(build_note_id("2.1", 3), "2.1:n3")


# ===========================================================================
# Tests for tm_logic.find_note_by_id
# ===========================================================================

class TestFindNoteById(unittest.TestCase):
    """Tests for find_note_by_id function."""

    def setUp(self):
        self.t1 = Task(title="T1", comments=["Note A", "Note B"])
        self.t2 = Task(title="T2", comments=["Note C"])
        self.tasks_by_date = {None: [self.t1, self.t2]}
        assign_task_ids(self.tasks_by_date)

    def test_find_first_note(self):
        result = find_note_by_id(self.tasks_by_date, "1:n1")
        self.assertIsNotNone(result)
        task, idx, text = result
        self.assertIs(task, self.t1)
        self.assertEqual(idx, 0)
        self.assertEqual(text, "Note A")

    def test_find_second_note(self):
        result = find_note_by_id(self.tasks_by_date, "1:n2")
        self.assertIsNotNone(result)
        task, idx, text = result
        self.assertEqual(idx, 1)
        self.assertEqual(text, "Note B")

    def test_find_note_on_second_task(self):
        result = find_note_by_id(self.tasks_by_date, "2:n1")
        self.assertIsNotNone(result)
        task, idx, text = result
        self.assertIs(task, self.t2)
        self.assertEqual(text, "Note C")

    def test_note_index_out_of_range(self):
        self.assertIsNone(find_note_by_id(self.tasks_by_date, "1:n5"))

    def test_note_zero_index_invalid(self):
        # note indices are 1-based
        self.assertIsNone(find_note_by_id(self.tasks_by_date, "1:n0"))

    def test_nonexistent_task(self):
        self.assertIsNone(find_note_by_id(self.tasks_by_date, "99:n1"))

    def test_invalid_format(self):
        self.assertIsNone(find_note_by_id(self.tasks_by_date, "invalid"))


# ===========================================================================
# Tests for tm_logic.task_matches_search
# ===========================================================================

class TestTaskMatchesSearch(unittest.TestCase):
    """Tests for task_matches_search function."""

    def test_none_query(self):
        t = Task(title="Any task")
        self.assertTrue(task_matches_search(t, None))

    def test_empty_query(self):
        t = Task(title="Any task")
        self.assertTrue(task_matches_search(t, ""))

    def test_whitespace_query(self):
        t = Task(title="Any task")
        self.assertTrue(task_matches_search(t, "   "))

    def test_match_title(self):
        t = Task(title="Fix the login bug")
        self.assertTrue(task_matches_search(t, "login"))

    def test_no_match_title(self):
        t = Task(title="Fix the login bug")
        self.assertFalse(task_matches_search(t, "signup"))

    def test_match_comment(self):
        t = Task(title="Task", comments=["Check the database"])
        self.assertTrue(task_matches_search(t, "database"))

    def test_match_subtask_title(self):
        t = Task(title="Task", subtasks=[Subtask(title="Write #api docs")])
        self.assertTrue(task_matches_search(t, "docs"))

    def test_match_tag_in_title(self):
        t = Task(title="Fix #bug in system")
        self.assertTrue(task_matches_search(t, "#bug"))

    def test_match_tag_in_subtask(self):
        t = Task(title="Task", subtasks=[Subtask(title="Subtask #backend")])
        self.assertTrue(task_matches_search(t, "#backend"))

    def test_case_insensitive(self):
        t = Task(title="Important Feature")
        self.assertTrue(task_matches_search(t, "IMPORTANT"))

    def test_priority_high(self):
        t = Task(title="Task", priority="HIGH")
        self.assertTrue(task_matches_search(t, "priority:high"))

    def test_priority_mismatch(self):
        t = Task(title="Task", priority="LOW")
        self.assertFalse(task_matches_search(t, "priority:high"))

    def test_priority_any(self):
        t = Task(title="Task", priority="MEDIUM")
        self.assertTrue(task_matches_search(t, "priority:any"))

    def test_priority_any_no_priority(self):
        t = Task(title="Task", priority=None)
        self.assertFalse(task_matches_search(t, "priority:any"))

    def test_priority_none(self):
        t = Task(title="Task", priority=None)
        self.assertTrue(task_matches_search(t, "priority:none"))

    def test_priority_none_has_priority(self):
        t = Task(title="Task", priority="HIGH")
        self.assertFalse(task_matches_search(t, "priority:none"))

    def test_due_any(self):
        t = Task(title="Task", due_date=datetime(2025, 6, 1))
        self.assertTrue(task_matches_search(t, "due:any"))

    def test_due_any_no_due(self):
        t = Task(title="Task", due_date=None)
        self.assertFalse(task_matches_search(t, "due:any"))

    def test_due_none(self):
        t = Task(title="Task", due_date=None)
        self.assertTrue(task_matches_search(t, "due:none"))

    def test_due_none_has_due(self):
        t = Task(title="Task", due_date=datetime(2025, 6, 1))
        self.assertFalse(task_matches_search(t, "due:none"))

    def test_due_overdue(self):
        yesterday = datetime.now() - timedelta(days=1)
        t = Task(title="Task", due_date=yesterday)
        self.assertTrue(task_matches_search(t, "due:overdue"))

    def test_due_not_overdue(self):
        tomorrow = datetime.now() + timedelta(days=1)
        t = Task(title="Task", due_date=tomorrow)
        self.assertFalse(task_matches_search(t, "due:overdue"))

    def test_due_today(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        t = Task(title="Task", due_date=today)
        self.assertTrue(task_matches_search(t, "due:today"))

    def test_due_week(self):
        in_3_days = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=3)
        t = Task(title="Task", due_date=in_3_days)
        self.assertTrue(task_matches_search(t, "due:week"))

    def test_due_week_too_far(self):
        in_10_days = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=10)
        t = Task(title="Task", due_date=in_10_days)
        self.assertFalse(task_matches_search(t, "due:week"))

    def test_due_no_due_returns_false(self):
        t = Task(title="Task", due_date=None)
        self.assertFalse(task_matches_search(t, "due:overdue"))


# ===========================================================================
# Tests for tm_logic.normalize_recurrence_input
# ===========================================================================

class TestNormalizeRecurrenceInput(unittest.TestCase):
    """Tests for normalize_recurrence_input function."""

    def test_valid_daily(self):
        self.assertEqual(normalize_recurrence_input("daily"), "daily")

    def test_valid_weekly(self):
        self.assertEqual(normalize_recurrence_input("weekly"), "weekly")

    def test_valid_biweekly(self):
        self.assertEqual(normalize_recurrence_input("biweekly"), "biweekly")

    def test_valid_monthly(self):
        self.assertEqual(normalize_recurrence_input("monthly"), "monthly")

    def test_valid_yearly(self):
        self.assertEqual(normalize_recurrence_input("yearly"), "yearly")

    def test_case_insensitive(self):
        self.assertEqual(normalize_recurrence_input("DAILY"), "daily")

    def test_alias_d(self):
        self.assertEqual(normalize_recurrence_input("D"), "daily")

    def test_alias_w(self):
        self.assertEqual(normalize_recurrence_input("W"), "weekly")

    def test_alias_bw(self):
        self.assertEqual(normalize_recurrence_input("BW"), "biweekly")

    def test_alias_m(self):
        self.assertEqual(normalize_recurrence_input("M"), "monthly")

    def test_alias_y(self):
        self.assertEqual(normalize_recurrence_input("Y"), "yearly")

    def test_alias_lowercase(self):
        self.assertEqual(normalize_recurrence_input("d"), "daily")

    def test_with_whitespace(self):
        self.assertEqual(normalize_recurrence_input("  weekly  "), "weekly")

    def test_invalid(self):
        self.assertIsNone(normalize_recurrence_input("hourly"))

    def test_empty_string(self):
        self.assertIsNone(normalize_recurrence_input(""))


# ===========================================================================
# Tests for tm_logic.parse_new_command_args
# ===========================================================================

class TestParseNewCommandArgs(unittest.TestCase):
    """Tests for parse_new_command_args function."""

    def test_basic_title(self):
        title, state, target_date, due_date, priority, recurrence, err = parse_new_command_args("n My Task")
        self.assertEqual(title, "My Task")
        self.assertEqual(state, "BACKLOG")
        self.assertIsNone(target_date)
        self.assertIsNone(due_date)
        self.assertIsNone(priority)
        self.assertIsNone(recurrence)
        self.assertIsNone(err)

    def test_new_keyword(self):
        title, state, *_ = parse_new_command_args("new My Task")
        self.assertEqual(title, "My Task")

    def test_with_state(self):
        title, state, _, _, _, _, err = parse_new_command_args("n My Task --state IP")
        self.assertEqual(title, "My Task")
        self.assertEqual(state, "IN PROGRESS")
        self.assertIsNone(err)

    def test_with_state_short_flag(self):
        title, state, _, _, _, _, err = parse_new_command_args("n Task -s done")
        self.assertEqual(state, "DONE")
        self.assertIsNone(err)

    def test_with_date(self):
        title, state, target_date, _, _, _, err = parse_new_command_args("n Task --date 01/06/2025")
        self.assertEqual(target_date, datetime(2025, 6, 1))
        self.assertIsNone(err)

    def test_with_due(self):
        _, _, _, due_date, _, _, err = parse_new_command_args("n Task --due 25/12/2025")
        self.assertEqual(due_date, datetime(2025, 12, 25))
        self.assertIsNone(err)

    def test_with_priority(self):
        _, _, _, _, priority, _, err = parse_new_command_args("n Task --priority H")
        self.assertEqual(priority, "HIGH")
        self.assertIsNone(err)

    def test_with_priority_short_flag(self):
        _, _, _, _, priority, _, err = parse_new_command_args("n Task -p urgent")
        self.assertEqual(priority, "URGENT")
        self.assertIsNone(err)

    def test_with_recurrence(self):
        _, _, _, _, _, recurrence, err = parse_new_command_args("n Task --recur weekly")
        self.assertEqual(recurrence, "weekly")
        self.assertIsNone(err)

    def test_all_options(self):
        title, state, target_date, due_date, priority, recurrence, err = parse_new_command_args(
            "n Big Task --state IP --date 01/01/2025 --due 15/01/2025 --priority H --recur monthly"
        )
        self.assertEqual(title, "Big Task")
        self.assertEqual(state, "IN PROGRESS")
        self.assertEqual(target_date, datetime(2025, 1, 1))
        self.assertEqual(due_date, datetime(2025, 1, 15))
        self.assertEqual(priority, "HIGH")
        self.assertEqual(recurrence, "monthly")
        self.assertIsNone(err)

    def test_invalid_state_error(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --state INVALID")
        self.assertIsNotNone(err)
        self.assertIn("Invalid state", err)

    def test_invalid_date_error(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --date nope")
        self.assertIsNotNone(err)
        self.assertIn("Invalid date", err)

    def test_invalid_due_error(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --due baddate")
        self.assertIsNotNone(err)
        self.assertIn("Invalid due date", err)

    def test_invalid_priority_error(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --priority MEGA")
        self.assertIsNotNone(err)
        self.assertIn("Invalid priority", err)

    def test_invalid_recurrence_error(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --recur hourly")
        self.assertIsNotNone(err)
        self.assertIn("Invalid recurrence", err)

    def test_missing_state_value(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --state")
        self.assertIsNotNone(err)
        self.assertIn("Missing value for --state", err)

    def test_missing_date_value(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --date")
        self.assertIsNotNone(err)
        self.assertIn("Missing value for --date", err)

    def test_missing_due_value(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --due")
        self.assertIsNotNone(err)
        self.assertIn("Missing value for --due", err)

    def test_missing_priority_value(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --priority")
        self.assertIsNotNone(err)
        self.assertIn("Missing value for --priority", err)

    def test_missing_recur_value(self):
        _, _, _, _, _, _, err = parse_new_command_args("n Task --recur")
        self.assertIsNotNone(err)
        self.assertIn("Missing value for --recur", err)

    def test_wrong_command(self):
        _, _, _, _, _, _, err = parse_new_command_args("x task")
        self.assertIsNotNone(err)
        self.assertIn("Usage", err)

    def test_empty_command(self):
        _, _, _, _, _, _, err = parse_new_command_args("")
        self.assertIsNotNone(err)

    def test_empty_title(self):
        title, state, _, _, _, _, err = parse_new_command_args("n --state IP")
        # Title is empty string, state is parsed
        self.assertEqual(title, "")
        self.assertEqual(state, "IN PROGRESS")
        self.assertIsNone(err)

    def test_multi_word_state(self):
        _, state, _, _, _, _, err = parse_new_command_args("n Task --state in progress")
        self.assertEqual(state, "IN PROGRESS")
        self.assertIsNone(err)

    def test_quoted_title(self):
        title, _, _, _, _, _, err = parse_new_command_args('n "My complex title" --state done')
        self.assertEqual(title, "My complex title")
        self.assertIsNone(err)


# ===========================================================================
# Tests for tm_logic.get_pending_tasks
# ===========================================================================

class TestGetPendingTasks(unittest.TestCase):
    """Tests for get_pending_tasks function."""

    def test_all_pending(self):
        tasks = [Task(title="T1", state="BACKLOG"), Task(title="T2", state="IN PROGRESS")]
        result = get_pending_tasks({None: tasks})
        self.assertEqual(len(result), 2)

    def test_excludes_done(self):
        tasks = [Task(title="T1", state="DONE"), Task(title="T2", state="BACKLOG")]
        result = get_pending_tasks({None: tasks})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "T2")

    def test_excludes_cancelled(self):
        tasks = [Task(title="T1", state="CANCELLED")]
        result = get_pending_tasks({None: tasks})
        self.assertEqual(len(result), 0)

    def test_empty(self):
        result = get_pending_tasks({})
        self.assertEqual(result, [])

    def test_multiple_dates(self):
        d1 = datetime(2025, 1, 1)
        d2 = datetime(2025, 1, 2)
        tasks_by_date = {
            d1: [Task(title="T1", state="BACKLOG")],
            d2: [Task(title="T2", state="DONE"), Task(title="T3", state="WAITING")],
        }
        result = get_pending_tasks(tasks_by_date)
        self.assertEqual(len(result), 2)
        titles = [t.title for t in result]
        self.assertIn("T1", titles)
        self.assertIn("T3", titles)


# ===========================================================================
# Tests for tm_logic.get_id_width
# ===========================================================================

class TestGetIdWidth(unittest.TestCase):
    """Tests for get_id_width function."""

    def test_empty(self):
        self.assertEqual(get_id_width({}), 1)

    def test_single_task(self):
        self.assertEqual(get_id_width({None: [Task(title="T")]}), 1)

    def test_nine_tasks(self):
        tasks = [Task(title=f"T{i}") for i in range(9)]
        self.assertEqual(get_id_width({None: tasks}), 1)

    def test_ten_tasks(self):
        tasks = [Task(title=f"T{i}") for i in range(10)]
        self.assertEqual(get_id_width({None: tasks}), 2)

    def test_hundred_tasks(self):
        tasks = [Task(title=f"T{i}") for i in range(100)]
        self.assertEqual(get_id_width({None: tasks}), 3)

    def test_multiple_dates(self):
        d1 = datetime(2025, 1, 1)
        d2 = datetime(2025, 1, 2)
        tasks_by_date = {
            d1: [Task(title=f"T{i}") for i in range(5)],
            d2: [Task(title=f"T{i}") for i in range(5)],
        }
        self.assertEqual(get_id_width(tasks_by_date), 2)


if __name__ == "__main__":
    unittest.main()
