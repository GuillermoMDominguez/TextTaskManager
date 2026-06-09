"""Command dispatch and use-case handlers for the Task Manager CLI.

This module is a thin router that delegates to domain sub-modules:
  - tm_cmd_common   : shared dataclasses, rendering, utilities
  - tm_cmd_crud     : task CRUD (new, change state, add note, edit, delete, move, dup, sub, das)
  - tm_cmd_views    : view/display (quit, clear, help, refresh, views, stats, undo, find, agenda, day, check)
  - tm_cmd_features : extended features (archive, tpl, recur, tt, block, pom, bd, kb, pj, export, import, wr, sort, email)
  - tm_cmd_system   : system integrations (sync, config sync, sync status, config jira, jira, log)

For backward compatibility it re-exports the public API that task_manager.py and tests depend on.
"""

import re
import shlex
from typing import Optional

from .tm_cmd_common import (
    # Public dataclasses
    CommandContext,
    CommandOutcome,
    ViewState,
    # Rendering/utilities used by router
    Colors,
    _get_state_color_inline,
    _log,
    _refresh_and_render,
    _render,
    # Re-exports for backward compat (tests import from src.tm_commands)
    _apply_tags_to_text,
    _default_archive_path,
    _extract_inline_meta,
    _parse_meta_command,
    _strip_inline_tags,
    _strip_tags,
    clear_screen,
)
from .tm_cmd_crud import (
    handle_new,
    handle_change_state,
    handle_add_note,
    handle_edit,
    handle_delete,
    handle_move,
    handle_duplicate,
    handle_subtask,
    handle_done_all_subtasks,
)
from .tm_cmd_views import (
    handle_quit,
    handle_clear,
    handle_empty,
    handle_help,
    handle_refresh,
    handle_view_all,
    handle_view_pending,
    handle_view_progress,
    handle_view_testing,
    handle_stats,
    handle_undo,
    handle_filter_clear,
    handle_find,
    handle_agenda,
    handle_day,
    handle_check,
)
from .tm_cmd_features import (
    handle_archive,
    handle_template,
    handle_recurrence,
    handle_time_tracking,
    handle_block_del,
    handle_unblock,
    handle_block,
    handle_pomodoro,
    handle_burndown,
    handle_kanban,
    handle_project,
    handle_export,
    handle_import,
    handle_weekly_report,
    handle_sort,
    handle_email,
)
from .tm_cmd_system import (
    handle_sync,
    handle_config_sync,
    handle_sync_status,
    handle_config_jira,
    handle_jira,
    handle_log,
    handle_log_show,
    handle_log_hide,
    handle_log_clear,
)


# ─── Command Help (used by help system & autocomplete) ──────────────────────

COMMAND_HELP = {
    "n": {
        "syntax": "n <title> [--state <state>] [--date dd/mm/yyyy] [--due dd/mm/yyyy] [--priority <level>]",
        "description": "Create a new parent task.",
        "examples": [
            "n Prepare release notes --state backlog --due 10/06/2026 --priority high",
            "n Follow up customer issue --date 04/06/2026",
        ],
    },
    "cs": {
        "syntax": "cs <id> [state]",
        "description": "Change the state of a task or subtask.",
        "examples": ["cs 3 DONE", "cs 4.1"],
    },
    "an": {
        "syntax": "an <id> <note>",
        "description": "Add a note to a parent task.",
        "examples": ["an 3 Review blocker with #backend"],
    },
    "e": {
        "syntax": "e <id> [text] [--due x] [--priority x] [--tags x]",
        "description": "Edit task (form if no args, inline with text or metadata flags). Also: md/meta.",
        "examples": ["e 3", "e 3 New title", "e 3 --due 10/06/2026 --priority high", "e 3 --tags backend,qr"],
    },
    "del": {
        "syntax": "del <id|id:n#>",
        "description": "Delete task, subtask, or note (asks confirmation).",
        "examples": ["del 3", "del 3:n2"],
    },
    "mv": {
        "syntax": "mv <id> <dd/mm/yyyy>",
        "description": "Move a parent task to another date section (asks confirmation).",
        "examples": ["mv 3 10/06/2026"],
    },
    "dup": {
        "syntax": "dup <id> [dd/mm/yyyy]",
        "description": "Duplicate a parent task with notes and subtasks.",
        "examples": ["dup 3", "dup 3 12/06/2026"],
    },
    "sub": {
        "syntax": "sub <id> <title>",
        "description": "Add a subtask to a parent task.",
        "examples": ["sub 3 Review the document", "sub 1 Call supplier"],
    },
    "das": {
        "syntax": "das <id>",
        "description": "Mark all subtasks as DONE and auto-close parent when applicable.",
        "examples": ["das 3"],
    },
    "ar": {
        "syntax": "ar [dd/mm/yyyy]",
        "description": "Archive finished tasks up to optional date (asks confirmation).",
        "examples": ["ar", "ar 10/06/2026"],
    },
    "md": {
        "syntax": "md <id> [--due x] [--priority x] [--tags x]",
        "description": "Alias for 'e' — edit metadata (due/priority/tags).",
        "examples": ["md 3 --due 10/06/2026", "md 3.1 --priority high --tags qa"],
    },
    "ag": {
        "syntax": "ag [days]",
        "description": "Show due-date agenda for next N days (default 7).",
        "examples": ["ag", "ag 14"],
    },
    "day": {
        "syntax": "day [date]",
        "description": "Show tasks created on a date (default: today). Accepts natural dates.",
        "examples": ["day", "hoy", "day 03/06/2026", "day yesterday", "day friday"],
    },
    "ck": {
        "syntax": "ck",
        "description": "Run journal linter and show format/metadata issues.",
        "examples": ["ck"],
    },
    "u": {
        "syntax": "u",
        "description": "Undo last mutation in current session.",
        "examples": ["u"],
    },
    "f": {
        "syntax": "f <text|#tag|priority:...|due:...>",
        "description": "Filter visible tasks by query.",
        "examples": [
            "f #backend",
            "f priority:high",
            "f due:overdue",
            "f due:today",
            "f due:10/06/2026",
            "fc",
        ],
    },
    "fc": {
        "syntax": "fc",
        "description": "Clear current active filter.",
        "examples": ["fc"],
    },
    "se": {
        "syntax": "se [recipient]",
        "description": "Send pending tasks by email.",
        "examples": ["se", "se team@example.com"],
    },
    "kb": {
        "syntax": "kb",
        "description": "Show kanban board view.",
        "examples": ["kb"],
    },
    "pj": {
        "syntax": "pj [#tag]",
        "description": "Show project/tag view. Without argument lists all tags.",
        "examples": ["pj", "pj #backend", "pj backend"],
    },
    "export": {
        "syntax": "export <json|csv|md> [filepath]",
        "description": "Export tasks to file. Default saves next to journal.",
        "examples": ["export json", "export csv /tmp/tasks.csv", "export md"],
    },
    "import": {
        "syntax": "import <filepath>",
        "description": "Import tasks from JSON file.",
        "examples": ["import tasks.json"],
    },
    "wr": {
        "syntax": "wr [days]",
        "description": "Show weekly report (default: last 7 days).",
        "examples": ["wr", "wr 14"],
    },
    "sort": {
        "syntax": "sort <priority|due_date|state|none> [asc|desc]",
        "description": "Set task sort order for display.",
        "examples": ["sort priority", "sort due_date desc", "sort none"],
    },
    "tpl": {
        "syntax": "tpl [name] | tpl save <name> | tpl del <name>",
        "description": "Use, list, save, or delete task templates.",
        "examples": ["tpl", "tpl standup", "tpl save standup", "tpl del standup"],
    },
    "tt": {
        "syntax": "tt <id> <time> | tt <id> start | tt <id> stop",
        "description": "Log time spent on a task (e.g. 2h, 30m, 1h30m).",
        "examples": ["tt 3 1h30m", "tt 3 start", "tt 3 stop"],
    },
    "block": {
        "syntax": "block <id> <id> | block del <blocked_id> <blocker_id>",
        "description": "Mark first task as blocked by second, or remove a specific blocker.",
        "examples": ["block 3 5", "block del 3 5"],
    },
    "unblock": {
        "syntax": "unblock <id>",
        "description": "Remove ALL blockers from a task.",
        "examples": ["unblock 3"],
    },
    "pom": {
        "syntax": "pom [id] [minutes]",
        "description": "Start a pomodoro timer (default 25min). Logs time to task on completion.",
        "examples": ["pom", "pom 3", "pom 3 45"],
    },
    "bd": {
        "syntax": "bd [days]",
        "description": "Show burndown chart (default 14 days).",
        "examples": ["bd", "bd 7", "bd 30"],
    },
}


ALIAS_TO_HELP_KEY = {
    "new": "n",
    "change": "cs",
    "add": "an",
    "edit": "e",
    "delete": "del",
    "move": "mv",
    "reschedule": "mv",
    "duplicate": "dup",
    "done": "das",
    "archive": "ar",
    "meta": "e",
    "agenda": "ag",
    "today": "day",
    "hoy": "day",
    "check": "ck",
    "undo": "u",
    "find": "f",
    "send": "se",
    "kanban": "kb",
    "project": "pj",
    "weekly": "wr",
    "template": "tpl",
    "time": "tt",
    "blocker": "block",
    "pomodoro": "pom",
    "burndown": "bd",
}


# ─── Router-level helpers ───────────────────────────────────────────────────

def _resolve_help_key(raw_command_name: str) -> Optional[str]:
    """Resolve aliases to canonical command key used in COMMAND_HELP."""
    command = raw_command_name.strip().lower()
    if command in COMMAND_HELP:
        return command
    return ALIAS_TO_HELP_KEY.get(command)


def _extract_help_request(raw_command: str) -> Optional[str]:
    """Return command name if user requested inline command help with -h/--help."""
    try:
        tokens = shlex.split(raw_command)
    except ValueError:
        return None

    if len(tokens) != 2:
        return None

    if tokens[1] not in ("-h", "--help"):
        return None

    return tokens[0]


def _print_command_help(help_key: str) -> None:
    """Print detailed help for a specific command."""
    info = COMMAND_HELP.get(help_key)
    if not info:
        _log("error", f"No help available for that command.")
        return

    print(f"\n{Colors.HEADER}{Colors.BOLD}Command Help: {help_key}{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 72}{Colors.RESET}")
    print(f"{Colors.BOLD}Syntax:{Colors.RESET} {info['syntax']}")
    print(f"{Colors.BOLD}Description:{Colors.RESET} {info['description']}")
    print(f"{Colors.BOLD}Examples:{Colors.RESET}")
    for example in info["examples"]:
        print(f"  {example}")


# ─── Main command dispatcher ────────────────────────────────────────────────

def execute_command(
    raw_command: str, tasks_by_date: dict, view_state: ViewState, context: CommandContext
) -> CommandOutcome:
    """Execute a single user command and return updated state.

    Dispatches to domain-specific handler sub-modules in a fixed priority order.
    Each handler returns Optional[CommandOutcome] — None means "not my command".
    """
    command = raw_command.strip().lower()

    # Inline help: e.g. "n -h" or "block --help"
    requested_help = _extract_help_request(raw_command)
    if requested_help:
        help_key = _resolve_help_key(requested_help)
        if help_key:
            _print_command_help(help_key)
            return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)

    # ── Views: quit, clear, empty, help ──────────────────────────────
    result = handle_quit(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_clear(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_help(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_view_all(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_view_pending(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_stats(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_undo(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_filter_clear(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_check(command, tasks_by_date, view_state, context)
    if result:
        return result

    # ── CRUD ─────────────────────────────────────────────────────────
    result = handle_new(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_change_state(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_add_note(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_edit(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_delete(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_move(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_duplicate(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_subtask(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_done_all_subtasks(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    # ── Features ─────────────────────────────────────────────────────
    result = handle_archive(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_find(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_refresh(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_template(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_recurrence(raw_command, tasks_by_date, view_state, context, tasks_by_date)
    if result:
        return result

    result = handle_time_tracking(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    # block del must be checked before generic block
    result = handle_block_del(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_unblock(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_block(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_pomodoro(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_burndown(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_kanban(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_project(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_export(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_import(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_weekly_report(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_sort(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_view_progress(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_view_testing(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_empty(command, tasks_by_date, view_state, context)
    if result:
        return result

    # ── Agenda/Day ───────────────────────────────────────────────────
    result = handle_agenda(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_day(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    # ── System/Integrations ──────────────────────────────────────────
    result = handle_sync_status(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_sync(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_config_sync(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_config_jira(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_jira(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_log_show(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_log_hide(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_log_clear(command, tasks_by_date, view_state, context)
    if result:
        return result

    result = handle_log(command, tasks_by_date, view_state, context)
    if result:
        return result

    # ── Email ────────────────────────────────────────────────────────
    result = handle_email(raw_command, tasks_by_date, view_state, context)
    if result:
        return result

    _log("error", "Unknown command. Type 'help' for available commands.")
    return CommandOutcome(tasks_by_date, view_state)
