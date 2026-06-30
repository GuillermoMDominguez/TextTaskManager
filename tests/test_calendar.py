"""Tests for calendar data — get_calendar_data and CalendarData from tm_views_data.py."""

import unittest
from datetime import datetime, timedelta
from src.tm_models import Task
from src.tm_views_data import get_calendar_data, CalendarData, TaskViewItem


def _make_task(
    title="Task",
    state="TODO",
    task_id="1",
    due_date=None,
    priority=None,
    recurrence=None,
) -> Task:
    return Task(
        title=title,
        state=state,
        task_id=task_id,
        due_date=due_date,
        priority=priority,
        recurrence=recurrence,
    )


class TestGetCalendarData(unittest.TestCase):
    def test_returns_calendar_data(self):
        result = get_calendar_data({}, view="month", year=2026, month=6)
        self.assertIsInstance(result, CalendarData)
        self.assertEqual(result.view, "month")
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 6)

    def test_month_view_days_count(self):
        result = get_calendar_data({}, view="month", year=2026, month=6)
        self.assertEqual(len(result.days), 30)
        self.assertEqual(result.start_date, "01/06/2026")
        self.assertEqual(result.end_date, "30/06/2026")

    def test_february_leap_year(self):
        result = get_calendar_data({}, view="month", year=2024, month=2)
        self.assertEqual(len(result.days), 29)

    def test_february_non_leap(self):
        result = get_calendar_data({}, view="month", year=2025, month=2)
        self.assertEqual(len(result.days), 28)

    def test_week_view_days_count(self):
        result = get_calendar_data({}, view="week", year=2026, month=6, day=15)
        self.assertEqual(len(result.days), 7)
        # June 15 2026 is Monday → week is Mon 15 - Sun 21
        self.assertEqual(result.start_date, "15/06/2026")
        self.assertEqual(result.end_date, "21/06/2026")

    def test_week_uses_given_day(self):
        result = get_calendar_data({}, view="week", year=2026, month=6, day=18)
        self.assertEqual(result.start_date, "15/06/2026")
        self.assertEqual(result.end_date, "21/06/2026")

    def test_tasks_placed_by_due_date(self):
        tasks_by_date = {
            datetime(2026, 6, 1): [
                _make_task("Meeting", due_date=datetime(2026, 6, 5)),
                _make_task("No due", due_date=None),
            ]
        }
        result = get_calendar_data(tasks_by_date, view="month", year=2026, month=6)
        self.assertIn("05/06/2026", result.days)
        self.assertEqual(len(result.days["05/06/2026"]), 1)
        self.assertEqual(result.days["05/06/2026"][0].title, "Meeting")

    def test_tasks_outside_month_excluded(self):
        tasks_by_date = {
            datetime(2026, 5, 1): [
                _make_task("May Task", due_date=datetime(2026, 5, 15)),
            ]
        }
        result = get_calendar_data(tasks_by_date, view="month", year=2026, month=6)
        for day_tasks in result.days.values():
            self.assertEqual(len(day_tasks), 0)

    def test_tasks_without_due_excluded(self):
        tasks_by_date = {
            datetime(2026, 6, 1): [
                _make_task("No due", due_date=None),
            ]
        }
        result = get_calendar_data(tasks_by_date, view="month", year=2026, month=6)
        for day_tasks in result.days.values():
            self.assertEqual(len(day_tasks), 0)

    def test_empty_tasks_by_date(self):
        result = get_calendar_data({}, view="month", year=2026, month=6)
        for day_tasks in result.days.values():
            self.assertEqual(len(day_tasks), 0)

    def test_defaults_to_current_month(self):
        now = datetime.now()
        result = get_calendar_data({})
        self.assertEqual(result.year, now.year)
        self.assertEqual(result.month, now.month)
        self.assertEqual(result.view, "month")

    def test_week_crosses_month_boundary(self):
        # June 29 2026 is a Monday — week spans June 29 → July 5
        result = get_calendar_data({}, view="week", year=2026, month=6, day=29)
        self.assertEqual(result.start_date, "29/06/2026")
        self.assertEqual(result.end_date, "05/07/2026")
        self.assertEqual(len(result.days), 7)

    def test_year_boundary_week(self):
        # Dec 28 2026 is a Monday — week spans Dec 28 → Jan 3
        result = get_calendar_data({}, view="week", year=2026, month=12, day=28)
        self.assertEqual(result.start_date, "28/12/2026")
        self.assertEqual(result.end_date, "03/01/2027")
        self.assertEqual(len(result.days), 7)

    def test_tasks_in_week_view(self):
        tasks_by_date = {
            datetime(2026, 6, 15): [
                _make_task("Mon Task", due_date=datetime(2026, 6, 15)),
                _make_task("Wed Task", due_date=datetime(2026, 6, 17)),
            ]
        }
        result = get_calendar_data(tasks_by_date, view="week", year=2026, month=6, day=15)
        self.assertEqual(len(result.days["15/06/2026"]), 1)
        self.assertEqual(len(result.days["17/06/2026"]), 1)
        self.assertEqual(result.days["15/06/2026"][0].title, "Mon Task")

    def test_return_type_is_calendar_data(self):
        result = get_calendar_data({})
        self.assertIsInstance(result, CalendarData)
        self.assertIsInstance(result.days, dict)
        for key, val in result.days.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(val, list)

    def test_task_view_item_fields(self):
        due = datetime(2026, 6, 10)
        tasks_by_date = {
            datetime(2026, 6, 1): [
                _make_task("Test", state="DONE", due_date=due, priority="HIGH"),
            ]
        }
        result = get_calendar_data(tasks_by_date, view="month", year=2026, month=6)
        item = result.days["10/06/2026"][0]
        self.assertIsInstance(item, TaskViewItem)
        self.assertEqual(item.title, "Test")
        self.assertEqual(item.state, "DONE")
        self.assertEqual(item.priority, "HIGH")


if __name__ == "__main__":
    unittest.main()
