"""System/integration command handlers: sync, config sync, sync status, config jira, jira, log."""

from pathlib import Path
from typing import Optional

from .tm_cmd_common import (
    CommandContext,
    CommandOutcome,
    ViewState,
    _log,
)


def handle_sync(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'sync' command."""
    if command != "sync":
        return None

    from .tm_sync import sync_push_blocking, is_configured
    if not is_configured():
        _log("info", f"Sync not configured. Use 'config sync' to set up.")
        return CommandOutcome(tasks_by_date, view_state)
    else:
        sync_push_blocking()
        # Refresh tasks — pull may have brought new data
        updated_tasks = context.refresh_tasks()
        return CommandOutcome(updated_tasks, view_state)


def handle_config_sync(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'config sync' command."""
    if command != "config sync":
        return None

    from .tm_sync import run_config_wizard, init_sync, sync_push_async, is_configured
    from .tm_settings import load_settings, save_settings
    from .tm_journal import register_post_write_hook

    script_dir = Path(context.journal_path).parent.parent
    journals_dir = Path(context.journal_path).parent

    sync_config = run_config_wizard(script_dir, journals_dir)
    if sync_config:
        # Update settings file
        settings = load_settings(script_dir, force_reload=True)
        settings["sync"] = sync_config
        save_settings(settings, script_dir)

        # Activate sync if not already active
        if not is_configured():
            if init_sync(journals_dir, settings, script_dir):
                register_post_write_hook(sync_push_async)

        _log("info", f"Sync configuration saved to .ttm_config")
    return CommandOutcome(tasks_by_date, view_state)


def handle_sync_status(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'sync status' command."""
    if command != "sync status":
        return None

    from .tm_sync import sync_status
    print(f"  {sync_status()}")
    return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)


def handle_config_jira(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'config jira' command."""
    if command != "config jira":
        return None

    from .tm_jira import run_config_wizard, init_jira
    script_dir = Path(context.journal_path).parent.parent
    run_config_wizard(script_dir)
    return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)


def handle_jira(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'jira <sub>' or 'j <sub>' commands."""
    if command.startswith("jira"):
        sub = command[4:].strip()
    elif command == "j" or command.startswith("j "):
        sub = command[1:].strip()
    else:
        return None

    from .tm_jira import execute as jira_execute, is_configured as jira_is_configured, init_jira
    script_dir = Path(context.journal_path).parent.parent
    if not jira_is_configured():
        init_jira(script_dir)
    jira_execute(sub, tasks_by_date, context)
    return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)


def handle_log(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'log' command (show log history)."""
    if command != "log":
        return None

    from .tm_log import get_history
    history = get_history()
    if not history:
        print("  (no log entries)")
    else:
        for line in history:
            print(f"  {line}")
    return CommandOutcome(tasks_by_date, view_state, skip_redraw=True)


def handle_log_show(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'show log|log show|log on' command."""
    if command not in ("show log", "log show", "log on"):
        return None

    from .tm_log import set_visible
    set_visible(True)
    _log("info", "Log enabled.")
    return CommandOutcome(tasks_by_date, view_state)


def handle_log_hide(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'hide log|log hide|log off' command."""
    if command not in ("hide log", "log hide", "log off"):
        return None

    from .tm_log import set_visible
    set_visible(False)
    return CommandOutcome(tasks_by_date, view_state)


def handle_log_clear(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'log clear|clear log' command."""
    if command not in ("log clear", "clear log"):
        return None

    from .tm_log import clear
    clear()
    return CommandOutcome(tasks_by_date, view_state)


def handle_web(
    command: str,
    tasks_by_date: dict,
    view_state: ViewState,
    context: CommandContext,
) -> Optional[CommandOutcome]:
    """Handle 'web' command — launch the web UI server."""
    if command != "web":
        return None

    from .tm_web import start_server
    start_server(context.journal_path)
    return CommandOutcome(tasks_by_date, view_state)
