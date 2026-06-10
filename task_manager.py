#!/usr/bin/env python3
"""Task Manager CLI entrypoint."""

import argparse
import sys
import atexit
from pathlib import Path
from typing import List, Optional

from src.tm_commands import CommandContext, ViewState, execute_command
from src.tm_config import APP_VERSION, BANNER_INNER_WIDTH, DEFAULT_STATE
from src.tm_features import sort_tasks
from src.tm_email import load_email_config
from src.tm_journal import JournalError, parse_journal, add_task_to_file, register_post_write_hook
from src.tm_log import log as tm_log_msg, get_status_line, set_visible as set_log_visible
from src.tm_logic import assign_task_ids, normalize_state_input, normalize_priority_input, parse_date_input
from src.tm_settings import load_settings
from src.tm_sync import init_sync, sync_pull, sync_push_async, shutdown as sync_shutdown, get_sync_user
from src.tm_ui import (
    Colors,
    clear_screen,
    display_tasks,
    enable_command_history,
    enable_windows_ansi,
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
    parser.add_argument("--web", action="store_true", help="Launch web UI instead of terminal interface")
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

    script_dir = Path(__file__).resolve().parent
    journals_dir = script_dir / "journals"
    cache_path = script_dir / ".last_journal"
    history_path = script_dir / ".task_manager_history"

    # Load user settings (create default config if missing)
    settings = load_settings(script_dir)
    config_path = script_dir / ".ttm_config"
    if not config_path.exists():
        from src.tm_settings import DEFAULT_SETTINGS, save_settings
        save_settings(DEFAULT_SETTINGS, script_dir)
        print(f"{Colors.DIM}Created default config: {config_path}{Colors.RESET}")

    # Clear screen on startup and register cleanup
    set_terminal_background()
    atexit.register(reset_terminal_background)

    # Apply log visibility from config
    set_log_visible(settings.get("show_log", True))

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
            from src.tm_logic import normalize_recurrence_input
            recurrence = normalize_recurrence_input(args.quick_recur)
            if not recurrence:
                print(f"{Colors.ERROR}Invalid recurrence: {args.quick_recur}{Colors.RESET}")
                sys.exit(1)

        if add_task_to_file(str(journal_path), args.quick_add, state or DEFAULT_STATE, target_date, due_date, priority, recurrence):
            print(f"{Colors.DIM}Task added to {journal_path.name}: \"{args.quick_add}\"{Colors.RESET}")
            sys.exit(0)
        else:
            print(f"{Colors.ERROR}Could not add task.{Colors.RESET}")
            sys.exit(1)

    # ─── Check / Fix mode (non-interactive) ────────────────────────────
    if args.check or args.fix:
        from src.tm_integrity import check_and_fix_journal
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

    # ─── Web UI mode (non-interactive) ─────────────────────────────────
    if args.web:
        journal_path = _resolve_journal_for_quick_ops(journals_dir, cache_path, args.journal)
        if journal_path is None or not journal_path.exists():
            print(f"{Colors.ERROR}No journal found. Run interactively first to create one.{Colors.RESET}")
            sys.exit(1)

        from src.tm_web import start_server
        start_server(str(journal_path))
        sys.exit(0)

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
    from src.tm_integrity import check_and_fix_journal
    issues, fixed = check_and_fix_journal(journal_path, auto_fix=True)
    if fixed > 0:
        print(f"{Colors.HEADER}Auto-fixed {fixed} issue(s) in journal:{Colors.RESET}")
        for issue in issues:
            print(f"  {Colors.DIM}{issue}{Colors.RESET}")

    # ─── Sync initialization ──────────────────────────────────────────
    sync_active = init_sync(journals_dir, settings, script_dir)
    if sync_active:
        register_post_write_hook(sync_push_async)
        atexit.register(sync_shutdown)
        sync_pull(interactive=True)

    # Get git username for prompt (cached once at startup)
    _sync_user = get_sync_user() if sync_active else ""

    tasks_cache: Optional[dict] = None

    def refresh_tasks() -> dict:
        """Reload tasks from journal file and assign session IDs."""
        nonlocal tasks_cache
        try:
            tasks = parse_journal(journal_path)
        except JournalError:
            if tasks_cache is None:
                raise
            return tasks_cache
        assign_task_ids(tasks)
        tasks_cache = tasks
        return tasks

    def _render_view(tasks_by_date: dict, view_state) -> None:
        """Render tasks respecting all view_state flags (sort, filter, search)."""
        render_data = tasks_by_date
        if view_state.sort_by != "none":
            render_data = {}
            for date, tasks in tasks_by_date.items():
                render_data[date] = sort_tasks(list(tasks), view_state.sort_by, view_state.sort_direction)
        display_tasks(render_data, view_state.show_done, view_state.only_in_progress, view_state.only_testing, view_state.search_query)

    print(f"{Colors.HEADER}{Colors.BOLD}")
    title = f"Task Manager v{APP_VERSION}"
    from src.tm_ui import _term_width
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
    _render_view(tasks_by_date, view_state)
    command_context = CommandContext(
        journal_path=journal_path,
        email_config=email_config,
        refresh_tasks=refresh_tasks,
        undo_stack=[],
        max_undo=settings.get("max_undo", 20),
    )

    # ─── Prompt builder ────────────────────────────────────────────────
    import time as _time_mod
    import re as _re_mod

    _prompt_format = settings.get("prompt_format", "{user} {time} > ")
    _prompt_colors = settings.get("prompt_colors", {})

    # Named color presets (ANSI basic + common names)
    _NAMED_COLORS = {
        "black": "\033[30m", "red": "\033[31m", "green": "\033[32m",
        "yellow": "\033[33m", "blue": "\033[34m", "magenta": "\033[35m",
        "cyan": "\033[36m", "white": "\033[37m", "gray": "\033[90m",
        "grey": "\033[90m", "bright_red": "\033[91m", "bright_green": "\033[92m",
        "bright_yellow": "\033[93m", "bright_blue": "\033[94m",
        "bright_magenta": "\033[95m", "bright_cyan": "\033[96m",
        "bright_white": "\033[97m", "dim": "\033[2m", "bold": "\033[1m",
    }

    def _parse_color(color_str: str) -> str:
        """Parse a color value: named color, or 'R,G,B' for 24-bit."""
        if not color_str:
            return ""
        # Check named colors first
        name = color_str.strip().lower().replace(" ", "_")
        if name in _NAMED_COLORS:
            return _NAMED_COLORS[name]
        # Try R,G,B format
        try:
            r, g, b = (int(x.strip()) for x in color_str.split(","))
            return f"\033[38;2;{r};{g};{b}m"
        except (ValueError, TypeError):
            return ""

    # Pre-resolve color escapes for each token
    _token_colors = {
        "user": _parse_color(_prompt_colors.get("user", "bright_green")),
        "time": _parse_color(_prompt_colors.get("time", "green")),
        "date": _parse_color(_prompt_colors.get("date", "green")),
        "journal": _parse_color(_prompt_colors.get("journal", "green")),
    }
    _color_sep = _parse_color(_prompt_colors.get("separator", "gray"))
    # Use plain \033[0m in prompt (no BG sequence — avoids true-color issues)
    _prompt_reset = "\033[0m"

    # Detect macOS libedit vs GNU readline for prompt strategy
    try:
        import readline as _readline_mod
        _is_libedit = "libedit" in (_readline_mod.__doc__ or "")
    except ImportError:
        _readline_mod = None
        _is_libedit = False

    def _build_prompt_char() -> tuple:
        """Build the input prompt from the configured format string.

        Returns (readline_safe, raw_colored, plain):
        - readline_safe: colored prompt with ANSI escapes wrapped in \\001/\\002
          so GNU readline can correctly calculate cursor position during
          history navigation (up/down arrows).
        - raw_colored: bare ANSI escapes without \\001/\\002 wrappers (for
          macOS libedit which strips \\001/\\002 content instead of passing
          it through to the terminal).
        - plain: uncolored text for width calculations.
        Supported placeholders: {user}, {time}, {date}, {journal}.
        """
        now = _time_mod.localtime()

        values = {
            "user": _sync_user,
            "time": _time_mod.strftime("%H:%M", now),
            "date": _time_mod.strftime(settings.get("date_format", "%d/%m/%Y"), now),
            "journal": Path(journal_path).stem,
        }

        # Split format into segments: alternating literal / {token}
        # We only switch foreground colors (no reset between tokens) to avoid
        # clearing the background mid-prompt.
        segments = _re_mod.split(r"(\{[^}]+\})", _prompt_format)

        readline_parts: list = []
        raw_parts: list = []
        plain_parts: list = []
        for seg in segments:
            if seg.startswith("{") and seg.endswith("}"):
                key = seg[1:-1]
                value = values.get(key, "")
                if not value:
                    continue
                color = _token_colors.get(key, _color_sep)
                readline_parts.append(f"\001{color}\002{value}")
                raw_parts.append(f"{color}{value}")
                plain_parts.append(value)
            elif seg:
                readline_parts.append(f"\001{_color_sep}\002{seg}")
                raw_parts.append(f"{_color_sep}{seg}")
                plain_parts.append(seg)

        # Single reset at the very end
        readline_colored = "".join(readline_parts) + f"\001{_prompt_reset}\002"
        raw_colored = "".join(raw_parts) + _prompt_reset
        plain = "".join(plain_parts)

        # Collapse double spaces from removed empty tokens
        while "  " in plain:
            plain = plain.replace("  ", " ")
        if plain.startswith(" "):
            plain = plain.lstrip(" ")
            # Also strip leading separator segment from both colored variants
            leading_rl = f"\001{_color_sep}\002 "
            if readline_colored.startswith(leading_rl):
                readline_colored = readline_colored[len(leading_rl):]
            leading_raw = f"{_color_sep} "
            if raw_colored.startswith(leading_raw):
                raw_colored = raw_colored[len(leading_raw):]

        return (readline_colored, raw_colored, plain)

    # ───────────────────────────────────────────────────────────────────

    while True:
        try:
            # Build prompt — include status line above input if there's a message
            status = get_status_line()
            readline_colored, raw_colored, plain = _build_prompt_char()
            if status:
                sys.stdout.write(f"\n{status}\n")
                sys.stdout.flush()
            else:
                sys.stdout.write("\n")
                sys.stdout.flush()

            if _is_libedit:
                # macOS libedit on Python 3.14+: \001/\002 wrappers no longer
                # pass ANSI escapes through to the terminal. Use raw colored
                # prompt (without wrappers) so colors actually render.
                raw_command = input(raw_colored).strip()
            else:
                # GNU readline: \001/\002 wrappers work correctly — ANSI
                # escapes pass through to terminal and are excluded from
                # width calculations.
                raw_command = input(readline_colored).strip()
            remember_command(raw_command)

            try:
                outcome = execute_command(raw_command, tasks_by_date, view_state, command_context)
            except JournalError as exc:
                tm_log_msg("error", str(exc))
                outcome = None
            except Exception as exc:
                import traceback
                crash_log = script_dir / "src" / "ttm_crash.log"
                try:
                    crash_log.write_text(
                        f"COMMAND ERROR ({raw_command}):\n{traceback.format_exc()}",
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                tm_log_msg("error", f"Unexpected error. See {crash_log}")
                outcome = None

            if outcome:
                tasks_by_date = outcome.tasks_by_date
                view_state = outcome.view_state

                if outcome.should_exit:
                    save_command_history(str(history_path))
                    print(f"{Colors.DIM}Goodbye!{Colors.RESET}")
                    break

                if outcome.skip_redraw:
                    # Command printed its own output (help, kb, stats) — don't overwrite
                    continue

            # Re-render: clean screen, fresh content, one prompt next iteration
            clear_screen()
            _render_view(tasks_by_date, view_state)

        except KeyboardInterrupt:
            save_command_history(str(history_path))
            break
        except EOFError:
            save_command_history(str(history_path))
            break


if __name__ == "__main__":
    main()
