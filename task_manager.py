#!/usr/bin/env python3
"""Task Manager CLI entrypoint."""

import argparse
import sys
import atexit
from pathlib import Path
from typing import List, Optional

from tm_commands import CommandContext, ViewState, execute_command
from tm_config import APP_VERSION, BANNER_INNER_WIDTH
from tm_email import load_email_config
from tm_journal import JournalError, parse_journal, add_task_to_file
from tm_logic import assign_task_ids, normalize_state_input, normalize_priority_input, parse_date_input
from tm_settings import load_settings
from tm_ui import (
    Colors,
    display_tasks,
    enable_command_history,
    enable_windows_ansi,
    init_background_color,
    set_terminal_background,
    reset_terminal_background,
    remember_command,
    save_command_history,
)


def normalize_journal_name(raw_name: str) -> Optional[str]:
    """Normalize a journal filename and enforce .txt extension."""
    name = raw_name.strip()
    if not name:
        return None

    path_like = Path(name)
    if path_like.name != name:
        return None

    if not name.lower().endswith(".txt"):
        name += ".txt"

    return name


def load_cached_journal(cache_path: Path) -> Optional[str]:
    """Load the cached journal filename if available."""
    try:
        if not cache_path.exists():
            return None
        cached = cache_path.read_text(encoding="utf-8").strip()
        return cached or None
    except OSError:
        return None


def save_cached_journal(cache_path: Path, journal_name: str) -> None:
    """Persist the last opened journal filename."""
    try:
        cache_path.write_text(f"{journal_name}\n", encoding="utf-8")
    except OSError as exc:
        print(f"{Colors.ERROR}Warning: could not save journal cache ({exc}).{Colors.RESET}")


def list_journals(journals_dir: Path) -> List[Path]:
    """Return sorted list of .txt journal files inside the journals directory."""
    try:
        return sorted([p for p in journals_dir.glob("*.txt") if p.is_file()], key=lambda p: p.name.lower())
    except OSError:
        return []


def migrate_legacy_journals(script_dir: Path, journals_dir: Path) -> None:
    """Move legacy root-level .txt journals into the journals directory."""
    try:
        legacy_files = [
            p
            for p in script_dir.glob("*.txt")
            if p.is_file() and p.parent == script_dir and p.name.lower() != "readme.txt"
        ]
    except OSError:
        return

    for legacy_file in legacy_files:
        target = journals_dir / legacy_file.name
        if target.exists():
            try:
                legacy_stat = legacy_file.stat()
                target_stat = target.stat()
                if legacy_stat.st_mtime > target_stat.st_mtime:
                    target.write_text(legacy_file.read_text(encoding="utf-8"), encoding="utf-8")
                    print(f"{Colors.DIM}Synced newer legacy journal to {target}.{Colors.RESET}")
            except OSError as exc:
                print(f"{Colors.ERROR}Warning: could not sync {legacy_file.name} ({exc}).{Colors.RESET}")
            continue
        try:
            legacy_file.replace(target)
            print(f"{Colors.DIM}Moved legacy journal to {target}.{Colors.RESET}")
        except OSError as exc:
            print(f"{Colors.ERROR}Warning: could not move {legacy_file.name} ({exc}).{Colors.RESET}")


def create_empty_journal(journals_dir: Path) -> Optional[Path]:
    """Ask the user for a journal name, create an empty file, and return its path."""
    while True:
        raw_name = input(f"{Colors.BOLD}Journal name (.txt optional): {Colors.RESET}")
        normalized = normalize_journal_name(raw_name)
        if not normalized:
            print(f"{Colors.ERROR}Invalid name. Use only a file name, e.g. my_journal.txt{Colors.RESET}")
            continue

        journal_path = journals_dir / normalized
        try:
            journal_path.touch(exist_ok=True)
            return journal_path
        except OSError as exc:
            print(f"{Colors.ERROR}Could not create journal: {exc}{Colors.RESET}")


def choose_journal(journals_dir: Path, cached_name: Optional[str]) -> Optional[Path]:
    """List journals and let user choose one, or create a new one."""
    journals = list_journals(journals_dir)
    cached_path = journals_dir / cached_name if cached_name else None
    default_journal = cached_path if cached_path and cached_path.exists() else (journals[0] if journals else None)

    if not journals:
        print(f"{Colors.DIM}No journals found in {journals_dir}.{Colors.RESET}")
        return create_empty_journal(journals_dir)

    while True:
        print(f"\n{Colors.HEADER}{Colors.BOLD}Available journals:{Colors.RESET}")
        for idx, path in enumerate(journals, start=1):
            marker = " (default)" if default_journal and path == default_journal else ""
            print(f"  {idx}. {path.name}{marker}")
        print(f"  n. Create new journal")

        prompt = "Choose journal number"
        if default_journal:
            prompt += " (Enter for default)"
        prompt += ": "

        answer = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip().lower()
        if answer == "" and default_journal:
            return default_journal

        if answer == "n":
            created = create_empty_journal(journals_dir)
            if created:
                return created
            continue

        if answer.isdigit():
            idx = int(answer)
            if 1 <= idx <= len(journals):
                return journals[idx - 1]

        print(f"{Colors.ERROR}Invalid option. Try again.{Colors.RESET}")


def resolve_journal_from_arg(arg_value: str, journals_dir: Path) -> Optional[Path]:
    """Resolve a journal path from CLI argument.

    If a plain filename is provided, it is interpreted inside journals_dir.
    """
    arg = arg_value.strip()
    if not arg:
        return None

    candidate = Path(arg)
    if candidate.name != arg:
        return None

    normalized = normalize_journal_name(candidate.name)
    if not normalized:
        return None
    return journals_dir / normalized


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for quick-add and other non-interactive operations."""
    parser = argparse.ArgumentParser(
        prog="task_manager",
        description="Task Manager CLI - manage tasks in plain text journals.",
        add_help=False,
    )
    parser.add_argument("journal", nargs="?", default=None, help="Journal filename to open")
    parser.add_argument("--add", "-a", dest="quick_add", help="Quick-add a task without entering interactive mode")
    parser.add_argument("--state", "-s", dest="quick_state", default=None, help="State for quick-add (default: BACKLOG)")
    parser.add_argument("--due", dest="quick_due", default=None, help="Due date for quick-add (dd/mm/yyyy)")
    parser.add_argument("--priority", "-p", dest="quick_priority", default=None, help="Priority for quick-add")
    parser.add_argument("--recur", dest="quick_recur", default=None, help="Recurrence for quick-add")
    parser.add_argument("--date", "-d", dest="quick_date", default=None, help="Target date section for quick-add (dd/mm/yyyy)")
    parser.add_argument("--check", action="store_true", help="Run integrity check and exit")
    parser.add_argument("--fix", action="store_true", help="Run integrity check with auto-fix and exit")
    parser.add_argument("--help", "-h", action="store_true", help="Show help")
    return parser


def _resolve_journal_for_quick_ops(journals_dir: Path, cache_path: Path, journal_arg: Optional[str]) -> Optional[Path]:
    """Resolve journal path for non-interactive operations."""
    if journal_arg:
        normalized = normalize_journal_name(journal_arg)
        if normalized:
            path = journals_dir / normalized
            if path.exists():
                return path
    # Fall back to cached
    cached = load_cached_journal(cache_path)
    if cached:
        path = journals_dir / cached
        if path.exists():
            return path
    # Fall back to first journal
    journals = list_journals(journals_dir)
    return journals[0] if journals else None


def main() -> None:
    """Main entry point for the task manager."""
    enable_windows_ansi()

    script_dir = Path(__file__).parent
    journals_dir = script_dir / "journals"
    cache_path = script_dir / ".last_journal"
    history_path = script_dir / ".task_manager_history"

    # Load user settings (create default config if missing)
    settings = load_settings(script_dir)
    config_path = script_dir / ".ttm_config"
    if not config_path.exists():
        from tm_settings import DEFAULT_SETTINGS, save_settings
        save_settings(DEFAULT_SETTINGS, script_dir)
        print(f"{Colors.DIM}Created default config: {config_path}{Colors.RESET}")

    # Apply background color from config and set terminal
    init_background_color(settings.get("background_color", "0,0,0"))
    set_terminal_background()
    atexit.register(reset_terminal_background)

    email_config = load_email_config([
        script_dir / ".task_manager_email.json",
        Path.home() / ".task_manager_email.json",
    ])

    try:
        journals_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"{Colors.ERROR}Error: could not create journals directory ({exc}).{Colors.RESET}")
        sys.exit(1)

    migrate_legacy_journals(script_dir, journals_dir)

    # Parse CLI arguments
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.help:
        parser.print_help()
        sys.exit(0)

    # ─── Quick-add mode (non-interactive) ──────────────────────────────
    if args.quick_add:
        journal_path = _resolve_journal_for_quick_ops(journals_dir, cache_path, args.journal)
        if journal_path is None or not journal_path.exists():
            print(f"{Colors.ERROR}No journal found. Run interactively first to create one.{Colors.RESET}")
            sys.exit(1)

        # Parse optional flags
        state = None
        if args.quick_state:
            state = normalize_state_input(args.quick_state)
            if not state:
                print(f"{Colors.ERROR}Invalid state: {args.quick_state}{Colors.RESET}")
                sys.exit(1)

        due_date = None
        if args.quick_due:
            due_date = parse_date_input(args.quick_due)
            if not due_date:
                print(f"{Colors.ERROR}Invalid due date: {args.quick_due} (use dd/mm/yyyy){Colors.RESET}")
                sys.exit(1)

        priority = None
        if args.quick_priority:
            priority = normalize_priority_input(args.quick_priority)
            if not priority:
                print(f"{Colors.ERROR}Invalid priority: {args.quick_priority}{Colors.RESET}")
                sys.exit(1)

        target_date = None
        if args.quick_date:
            target_date = parse_date_input(args.quick_date)
            if not target_date:
                print(f"{Colors.ERROR}Invalid date: {args.quick_date} (use dd/mm/yyyy){Colors.RESET}")
                sys.exit(1)

        recurrence = None
        if args.quick_recur:
            from tm_logic import normalize_recurrence_input
            recurrence = normalize_recurrence_input(args.quick_recur)
            if not recurrence:
                print(f"{Colors.ERROR}Invalid recurrence: {args.quick_recur}{Colors.RESET}")
                sys.exit(1)

        if add_task_to_file(str(journal_path), args.quick_add, state or "BACKLOG", target_date, due_date, priority, recurrence):
            print(f"{Colors.DIM}Task added to {journal_path.name}: \"{args.quick_add}\"{Colors.RESET}")
            sys.exit(0)
        else:
            print(f"{Colors.ERROR}Could not add task.{Colors.RESET}")
            sys.exit(1)

    # ─── Check / Fix mode (non-interactive) ────────────────────────────
    if args.check or args.fix:
        from tm_integrity import check_and_fix_journal
        journal_path = _resolve_journal_for_quick_ops(journals_dir, cache_path, args.journal)
        if journal_path is None or not journal_path.exists():
            print(f"{Colors.ERROR}No journal found.{Colors.RESET}")
            sys.exit(1)

        issues, fixed = check_and_fix_journal(str(journal_path), auto_fix=args.fix)
        if not issues:
            print(f"{Colors.DIM}Journal integrity check passed. No issues found.{Colors.RESET}")
        else:
            for issue in issues:
                print(f"  {issue}")
            if args.fix:
                print(f"\n{Colors.DIM}Fixed {fixed} issue(s).{Colors.RESET}")
            else:
                print(f"\n{Colors.HEADER}Found {len(issues)} issue(s). Run with --fix to auto-repair.{Colors.RESET}")
        sys.exit(0 if not issues or args.fix else 1)

    # ─── Interactive mode ──────────────────────────────────────────────
    if args.journal:
        selected_journal = resolve_journal_from_arg(args.journal, journals_dir)
        if selected_journal is None or not selected_journal.exists():
            print(f"{Colors.ERROR}Journal not found. Select one from journals folder or create a new one.{Colors.RESET}")
            selected_journal = choose_journal(journals_dir, load_cached_journal(cache_path))
    else:
        selected_journal = choose_journal(journals_dir, load_cached_journal(cache_path))

    if selected_journal is None:
        print(f"{Colors.ERROR}Could not open any journal.{Colors.RESET}")
        sys.exit(1)

    if not selected_journal.exists():
        print(f"{Colors.DIM}Journal not found. Creating a new empty journal file.{Colors.RESET}")
        selected_journal = create_empty_journal(journals_dir)
        if selected_journal is None:
            print(f"{Colors.ERROR}Could not create journal file.{Colors.RESET}")
            sys.exit(1)

    journal_path = str(selected_journal)
    save_cached_journal(cache_path, selected_journal.name)

    # ─── Integrity check on load ──────────────────────────────────────
    from tm_integrity import check_and_fix_journal
    issues, fixed = check_and_fix_journal(journal_path, auto_fix=True)
    if fixed > 0:
        print(f"{Colors.HEADER}Auto-fixed {fixed} issue(s) in journal:{Colors.RESET}")
        for issue in issues:
            print(f"  {Colors.DIM}{issue}{Colors.RESET}")

    tasks_cache: Optional[dict] = None

    def refresh_tasks() -> dict:
        """Reload tasks from journal file and assign session IDs."""
        nonlocal tasks_cache
        try:
            tasks = parse_journal(journal_path)
        except JournalError:
            if tasks_cache is None:
                raise
            raise
        assign_task_ids(tasks)
        tasks_cache = tasks
        return tasks

    print(f"{Colors.HEADER}{Colors.BOLD}")
    title = f"Task Manager v{APP_VERSION}"
    from tm_ui import _term_width
    bw = min(_term_width() - 4, 60)
    print(f"╔{'═' * bw}╗")
    print(f"║{title.center(bw)}║")
    print(f"╚{'═' * bw}╝")
    print(f"{Colors.RESET}")
    print(f"  Loading: {journal_path}")

    enable_command_history(str(history_path))

    try:
        tasks_by_date = refresh_tasks()
    except JournalError as exc:
        print(f"{Colors.ERROR}{exc}{Colors.RESET}")
        sys.exit(1)

    view_state = ViewState(
        sort_by=settings.get("sort_by", "none"),
        sort_direction=settings.get("sort_direction", "asc"),
    )
    display_tasks(tasks_by_date, view_state.show_done)
    command_context = CommandContext(
        journal_path=journal_path,
        email_config=email_config,
        refresh_tasks=refresh_tasks,
        undo_stack=[],
        max_undo=settings.get("max_undo", 20),
    )

    while True:
        try:
            raw_command = input(f"\n{Colors.BOLD}>{Colors.RESET} ")
            remember_command(raw_command)
            raw_command = raw_command.strip()

            try:
                outcome = execute_command(raw_command, tasks_by_date, view_state, command_context)
            except JournalError as exc:
                print(f"{Colors.ERROR}{exc}{Colors.RESET}")
                continue

            tasks_by_date = outcome.tasks_by_date
            view_state = outcome.view_state

            if outcome.should_exit:
                save_command_history(str(history_path))
                print(f"{Colors.DIM}Goodbye!{Colors.RESET}")
                break

        except KeyboardInterrupt:
            save_command_history(str(history_path))
            print(f"\n{Colors.DIM}Goodbye!{Colors.RESET}")
            break
        except EOFError:
            save_command_history(str(history_path))
            print(f"\n{Colors.DIM}Goodbye!{Colors.RESET}")
            break


if __name__ == "__main__":
    main()
