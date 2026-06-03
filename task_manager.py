#!/usr/bin/env python3
"""Task Manager CLI entrypoint."""

import sys
from pathlib import Path
from typing import List, Optional

from tm_commands import CommandContext, ViewState, execute_command
from tm_config import APP_VERSION, BANNER_INNER_WIDTH
from tm_email import load_email_config
from tm_journal import JournalError, parse_journal
from tm_logic import assign_task_ids
from tm_ui import (
    Colors,
    display_tasks,
    enable_command_history,
    enable_windows_ansi,
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


def main() -> None:
    """Main entry point for the task manager."""
    enable_windows_ansi()

    script_dir = Path(__file__).parent
    journals_dir = script_dir / "journals"
    cache_path = script_dir / ".last_journal"
    history_path = script_dir / ".task_manager_history"
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

    if len(sys.argv) > 1:
        selected_journal = resolve_journal_from_arg(sys.argv[1], journals_dir)
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
    print(f"╔{'═' * BANNER_INNER_WIDTH}╗")
    print(f"║{title.center(BANNER_INNER_WIDTH)}║")
    print(f"╚{'═' * BANNER_INNER_WIDTH}╝")
    print(f"{Colors.RESET}")
    print(f"  Loading: {journal_path}")

    enable_command_history(str(history_path))

    try:
        tasks_by_date = refresh_tasks()
    except JournalError as exc:
        print(f"{Colors.ERROR}{exc}{Colors.RESET}")
        sys.exit(1)

    view_state = ViewState()
    display_tasks(tasks_by_date, view_state.show_done)
    command_context = CommandContext(
        journal_path=journal_path,
        email_config=email_config,
        refresh_tasks=refresh_tasks,
        undo_stack=[],
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
