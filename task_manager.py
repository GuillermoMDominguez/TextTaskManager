#!/usr/bin/env python3
"""Task Manager CLI entrypoint."""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

from tm_config import APP_VERSION, BANNER_INNER_WIDTH, DEFAULT_STATE
from tm_journal import add_task_to_file, parse_journal, update_task_state_in_file
from tm_logic import assign_task_ids, find_task_by_id, normalize_state_input, parse_new_command_args
from tm_ui import (
    Colors,
    clear_screen,
    display_stats,
    display_tasks,
    enable_command_history,
    enable_windows_ansi,
    print_help,
    prompt_for_state,
    remember_command,
    save_command_history,
)


def main() -> None:
    """Main entry point for the task manager."""
    enable_windows_ansi()

    script_dir = Path(__file__).parent
    default_journal = script_dir / "Journal_2026.txt"
    history_path = script_dir / ".task_manager_history"

    import sys

    if len(sys.argv) > 1:
        journal_path = sys.argv[1]
    else:
        journal_path = str(default_journal)

    id_registry: Dict[Tuple[str, str, Tuple[str, ...], int], str] = {}
    next_task_id = 1

    def refresh_tasks() -> dict:
        """Reload tasks from journal file and assign execution IDs."""
        nonlocal next_task_id
        tasks = parse_journal(journal_path)
        next_task_id = assign_task_ids(tasks, id_registry, next_task_id)
        return tasks

    print(f"{Colors.HEADER}{Colors.BOLD}")
    title = f"Task Manager v{APP_VERSION}"
    print(f"╔{'═' * BANNER_INNER_WIDTH}╗")
    print(f"║{title.center(BANNER_INNER_WIDTH)}║")
    print(f"╚{'═' * BANNER_INNER_WIDTH}╝")
    print(f"{Colors.RESET}")
    print(f"  Loading: {journal_path}")

    enable_command_history(str(history_path))

    tasks_by_date = refresh_tasks()

    show_done = False
    only_in_progress = False
    only_testing = False
    display_tasks(tasks_by_date, show_done)

    print_help()

    while True:
        try:
            raw_command = input(f"\n{Colors.BOLD}>{Colors.RESET} ")
            remember_command(raw_command)
            raw_command = raw_command.strip()
            command = raw_command.lower()

            if command in ("q", "quit", "exit"):
                save_command_history(str(history_path))
                print(f"{Colors.DIM}Goodbye!{Colors.RESET}")
                break

            elif command in ("a", "all"):
                tasks_by_date = refresh_tasks()
                show_done = True
                only_in_progress = False
                only_testing = False
                clear_screen()
                display_tasks(tasks_by_date, show_done)

            elif command in ("p", "pending"):
                tasks_by_date = refresh_tasks()
                show_done = False
                only_in_progress = False
                only_testing = False
                clear_screen()
                display_tasks(tasks_by_date, show_done)

            elif command in ("s", "stats"):
                tasks_by_date = refresh_tasks()
                display_stats(tasks_by_date)

            elif re.match(r"^\s*(?:n|new)\b", raw_command, re.IGNORECASE):
                task_title, task_state, target_date, parse_error = parse_new_command_args(raw_command)
                if parse_error:
                    print(f"{Colors.ERROR}{parse_error}{Colors.RESET}")
                    print(f"{Colors.DIM}Usage: n [title] [--state <state>] [--date dd/mm/yyyy]{Colors.RESET}")
                    continue

                if not task_title:
                    task_title = input(f"{Colors.BOLD}Task title: {Colors.RESET}").strip()

                if not task_title:
                    print(f"{Colors.ERROR}Task title cannot be empty.{Colors.RESET}")
                    continue

                task_state = task_state or DEFAULT_STATE

                if add_task_to_file(journal_path, task_title, task_state, target_date):
                    tasks_by_date = refresh_tasks()
                    clear_screen()
                    created_date = (target_date or datetime.now()).strftime("%d/%m/%Y")
                    print(f"{Colors.DIM}Task created in {task_state} for {created_date}.{Colors.RESET}")
                    display_tasks(tasks_by_date, show_done, only_in_progress, only_testing)
                else:
                    print(f"{Colors.ERROR}Could not create task in file.{Colors.RESET}")

            elif re.match(r"^\s*(?:cs|change\s+state)\b", raw_command, re.IGNORECASE):
                tasks_by_date = refresh_tasks()
                match = re.match(r"^\s*(?:cs|change\s+state)\s+(\S+)(?:\s+(.+))?\s*$", raw_command, re.IGNORECASE)
                if not match:
                    print(f"{Colors.ERROR}Usage: cs <task_id> [state]{Colors.RESET}")
                    continue

                requested_id = match.group(1).strip()
                target_task = find_task_by_id(tasks_by_date, requested_id)
                if not target_task:
                    print(f"{Colors.ERROR}Task ID {requested_id} not found.{Colors.RESET}")
                    continue

                selected_state = None
                requested_state = match.group(2)
                if requested_state:
                    selected_state = normalize_state_input(requested_state)

                if not selected_state:
                    if requested_state:
                        print(f"{Colors.ERROR}Invalid state: {requested_state}{Colors.RESET}")
                    selected_state = prompt_for_state()

                if update_task_state_in_file(journal_path, target_task, selected_state):
                    tasks_by_date = refresh_tasks()
                    clear_screen()
                    print(f"{Colors.DIM}Task {requested_id} updated to {selected_state}.{Colors.RESET}")
                    display_tasks(tasks_by_date, show_done, only_in_progress, only_testing)
                else:
                    print(f"{Colors.ERROR}Could not update task in file.{Colors.RESET}")

            elif command in ("r", "refresh"):
                tasks_by_date = refresh_tasks()
                clear_screen()
                print(f"{Colors.DIM}Refreshed!{Colors.RESET}")
                display_tasks(tasks_by_date, show_done, only_in_progress, only_testing)

            elif command in ("h", "help", "?"):
                print_help()

            elif command in ("i", "progress"):
                tasks_by_date = refresh_tasks()
                clear_screen()
                show_done = False
                only_in_progress = True
                only_testing = False
                display_tasks(tasks_by_date, show_done, only_in_progress, only_testing)

            elif command in ("t", "testing"):
                tasks_by_date = refresh_tasks()
                clear_screen()
                show_done = False
                only_in_progress = False
                only_testing = True
                display_tasks(tasks_by_date, show_done, only_in_progress, only_testing)

            elif command == "":
                continue

            else:
                print(f"{Colors.ERROR}Unknown command. Type 'help' for available commands.{Colors.RESET}")

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
