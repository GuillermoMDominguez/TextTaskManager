#!/usr/bin/env python3
"""
Task Manager - A simple CLI app to manage tasks from daily notes.

Parses a journal file with the following format:
- Days marked with "## dd/mm/yyyy"
- Tasks marked with "- task title"
- States marked with "-- STATE" (BACKLOG, IN PROGRESS, WAITING, TESTING, DONE)
- Comments marked with ": comment text"
"""

import re
import os
import sys
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path


def enable_windows_ansi():
    """Enable ANSI escape code support on Windows."""
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Enable VIRTUAL_TERMINAL_PROCESSING for stdout
            STD_OUTPUT_HANDLE = -11
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            pass  # If it fails, colors just won't work


# Enable ANSI on Windows at import time
enable_windows_ansi()

# Valid states
VALID_STATES = ["BACKLOG", "IN PROGRESS", "WAITING", "TESTING", "DONE", "CANCELLED"]
# State aliases (alternative names that map to valid states)
STATE_ALIASES = {
    "IN TESTING": "TESTING",
}
# States that are considered "finished" (hidden by default)
FINISHED_STATES = ["DONE", "CANCELLED"]
DEFAULT_STATE = "BACKLOG"

# ANSI color codes for terminal output
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # State colors
    BACKLOG = "\033[90m"      # Gray
    IN_PROGRESS = "\033[33m"  # Yellow
    WAITING = "\033[35m"      # Magenta
    TESTING = "\033[36m"      # Cyan
    DONE = "\033[32m"         # Green
    CANCELLED = "\033[91m"    # Red
    
    # Other colors
    DATE = "\033[94m"         # Blue
    TASK = "\033[97m"         # White
    COMMENT = "\033[90m"      # Gray
    HEADER = "\033[96m"       # Cyan
    ERROR = "\033[91m"        # Red


@dataclass
class Task:
    """Represents a single task with its properties."""
    title: str
    state: str = DEFAULT_STATE
    comments: List[str] = field(default_factory=list)
    date: Optional[datetime] = None
    
    def is_finished(self) -> bool:
        """Check if task is in a finished state (DONE or CANCELLED)."""
        return self.state in FINISHED_STATES
    
    def is_in_progress(self) -> bool:
        return self.state == "IN PROGRESS"
    def is_in_testing(self) -> bool:
        return self.state == "TESTING" or self.state == "IN TESTING"


def get_state_color(state: str) -> str:
    """Get the color code for a state."""
    color_map = {
        "BACKLOG": Colors.BACKLOG,
        "IN PROGRESS": Colors.IN_PROGRESS,
        "WAITING": Colors.WAITING,
        "TESTING": Colors.TESTING,
        "DONE": Colors.DONE,
        "CANCELLED": Colors.CANCELLED,
    }
    return color_map.get(state, Colors.RESET)


def split_comments(text: str) -> List[str]:
    """Split a text by ':' into separate comments, filtering empty ones."""
    return [c.strip() for c in text.split(':') if c.strip()]


def parse_task_line(line: str) -> Optional[Task]:
    """
    Parse a single task line and extract title, state, and comments.
    
    Format: - Task title -- STATE : comment : comment2 -- STATE2 : comment3
    Also handles -> as an alternative state marker (same as --)
    """
    # Remove leading whitespace and check if it starts with a task marker
    stripped = line.strip()
    if not stripped.startswith('-'):
        return None
    
    # Remove the leading dash and space
    content = stripped[1:].strip()
    if not content:
        return None
    
    task = Task(title="")
    comments = []
    current_state = DEFAULT_STATE
    
    # Split by "--" or "->" to separate segments (state markers)
    # Each segment (except first) might contain a state or be part of text
    parts = re.split(r'\s*(?:--|->)\s*', content)
    
    # First part is the title (possibly with comments using :)
    title_part = parts[0]
    
    # Check if title has comments (using :)
    if ':' in title_part:
        idx = title_part.find(':')
        task.title = title_part[:idx].strip()
        # Split remaining by : for multiple comments
        comments.extend(split_comments(title_part[idx + 1:]))
    else:
        task.title = title_part.strip()
    
    # Process remaining parts (after -- or ->)
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
            
        # Check if this part starts with a valid state or state alias
        state_found = None
        remaining = part
        
        # Check aliases first (they may be longer, e.g., "IN TESTING" vs "TESTING")
        for alias, canonical in STATE_ALIASES.items():
            if part.upper().startswith(alias):
                state_found = canonical
                remaining = part[len(alias):].strip()
                break
        
        # If no alias matched, check valid states
        if not state_found:
            for state in VALID_STATES:
                if part.upper().startswith(state):
                    state_found = state
                    remaining = part[len(state):].strip()
                    break
        
        if state_found:
            current_state = state_found
            # Check if there's comments after the state (using :)
            remaining = remaining.lstrip()
            if remaining.startswith(':'):
                # Split by : for multiple comments
                comments.extend(split_comments(remaining[1:]))
            elif remaining:
                # Unrecognized text after state, treat as comment(s)
                comments.extend(split_comments(remaining))
        else:
            # No valid state found, treat whole part as comment(s)
            comments.extend(split_comments(part))
    
    task.state = current_state
    task.comments = comments
    
    return task


def parse_date(line: str) -> Optional[datetime]:
    """Parse a date line in format '## dd/mm/yyyy'."""
    match = re.match(r'^##\s*(\d{1,2}/\d{1,2}/\d{4})\s*$', line.strip())
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y")
        except ValueError:
            return None
    return None


def parse_journal(filepath: str) -> dict:
    """
    Parse the journal file and extract all tasks grouped by date.
    
    Returns a dictionary with dates as keys and list of tasks as values.
    """
    tasks_by_date = {}
    current_date = None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                # Check for date header
                date = parse_date(line)
                if date:
                    current_date = date
                    if current_date not in tasks_by_date:
                        tasks_by_date[current_date] = []
                    continue
                
                # Check for task line (starts with dash after optional whitespace)
                stripped = line.strip()
                if stripped.startswith('-') and not stripped.startswith('--'):
                    task = parse_task_line(line)
                    if task and task.title:
                        task.date = current_date
                        if current_date:
                            tasks_by_date[current_date].append(task)
                        else:
                            # Tasks without a date go under None
                            if None not in tasks_by_date:
                                tasks_by_date[None] = []
                            tasks_by_date[None].append(task)
    
    except FileNotFoundError:
        print(f"{Colors.ERROR}Error: File not found: {filepath}{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.ERROR}Error reading file: {e}{Colors.RESET}")
        sys.exit(1)
    
    return tasks_by_date


def format_state(state: str) -> str:
    """Format a state with color and padding."""
    color = get_state_color(state)
    return f"{color}{state:12}{Colors.RESET}"


def display_tasks(tasks_by_date: dict, show_done: bool = False,only_in_progress: bool = False,only_testing: bool = False):
    """Display tasks grouped by date in descending order."""
    # Sort dates in descending order (most recent first)
    sorted_dates = sorted(
        [d for d in tasks_by_date.keys() if d is not None],
        reverse=True
    )
    
    # Add None date at the end if exists
    if None in tasks_by_date:
        sorted_dates.append(None)
    
    total_tasks = 0
    total_pending = 0
    
    for date in sorted_dates:
        tasks = tasks_by_date[date]
        
        # Filter tasks based on show_done flag
        if not show_done:
            if not only_in_progress:
                if not only_testing:
                    visible_tasks = [t for t in tasks if not t.is_finished()]
                else:
                    visible_tasks = [t for t in tasks if t.is_in_testing()]
            else:
                visible_tasks = [t for t in tasks if t.is_in_progress() or t.is_in_testing()]
        else:
            visible_tasks = tasks
        
        if not visible_tasks:
            continue
        
        # Print date header
        if date:
            date_str = date.strftime("%A, %d/%m/%Y")
        else:
            date_str = "No Date"
        
        print(f"\n{Colors.DATE}{Colors.BOLD}{'─' * 50}{Colors.RESET}")
        print(f"{Colors.DATE}{Colors.BOLD}  {date_str}{Colors.RESET}")
        print(f"{Colors.DATE}{'─' * 50}{Colors.RESET}")
        
        for task in visible_tasks:
            total_tasks += 1
            if not task.is_finished():
                total_pending += 1
            
            # Print task with state
            state_display = format_state(task.state)
            print(f"  [{state_display}] {Colors.TASK}{task.title}{Colors.RESET}")
            
            # Print comments if any (each on its own bullet point)
            for comment in task.comments:
                print(f"      {Colors.COMMENT}- {comment}{Colors.RESET}")
    
    # Print summary
    print(f"\n{Colors.HEADER}{'─' * 50}{Colors.RESET}")
    if show_done:
        print(f"{Colors.HEADER}  Total: {total_tasks} tasks ({total_pending} pending){Colors.RESET}")
    else:
        print(f"{Colors.HEADER}  Showing: {total_pending} pending tasks{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 50}{Colors.RESET}")


def get_stats(tasks_by_date: dict) -> dict:
    """Calculate statistics about tasks."""
    stats = {state: 0 for state in VALID_STATES}
    total = 0
    
    for tasks in tasks_by_date.values():
        for task in tasks:
            stats[task.state] = stats.get(task.state, 0) + 1
            total += 1
    
    return {"by_state": stats, "total": total}


def display_stats(tasks_by_date: dict):
    """Display task statistics."""
    stats = get_stats(tasks_by_date)
    
    print(f"\n{Colors.HEADER}{Colors.BOLD}Task Statistics{Colors.RESET}")
    print(f"{Colors.HEADER}{'─' * 30}{Colors.RESET}")
    
    for state in VALID_STATES:
        count = stats["by_state"].get(state, 0)
        color = get_state_color(state)
        bar = "█" * count
        print(f"  {color}{state:12}{Colors.RESET} {count:3} {color}{bar}{Colors.RESET}")
    
    print(f"{Colors.HEADER}{'─' * 30}{Colors.RESET}")
    print(f"  {'Total':12} {stats['total']:3}")


def print_help():
    """Print help message."""
    print(f"""
{Colors.HEADER}{Colors.BOLD}Task Manager - Commands{Colors.RESET}
{Colors.HEADER}{'─' * 40}{Colors.RESET}
  {Colors.BOLD}a{Colors.RESET} / {Colors.BOLD}all{Colors.RESET}          Show all tasks (including done)
  {Colors.BOLD}p{Colors.RESET} / {Colors.BOLD}pending{Colors.RESET}      Show pending tasks only (default)
  {Colors.BOLD}i{Colors.RESET} / {Colors.BOLD}in progress{Colors.RESET}  Show in progress or testing tasks only
  {Colors.BOLD}t{Colors.RESET} / {Colors.BOLD}in testing{Colors.RESET}   Show in testing tasks only
  {Colors.BOLD}s{Colors.RESET} / {Colors.BOLD}stats{Colors.RESET}        Show task statistics
  {Colors.BOLD}r{Colors.RESET} / {Colors.BOLD}refresh{Colors.RESET}      Reload file and refresh display
  {Colors.BOLD}h{Colors.RESET} / {Colors.BOLD}help{Colors.RESET}         Show this help message
  {Colors.BOLD}q{Colors.RESET} / {Colors.BOLD}quit{Colors.RESET}         Exit the application
{Colors.HEADER}{'─' * 40}{Colors.RESET}
""")

def print_in_progress():
    pass

def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def main():
    """Main entry point for the task manager."""
    # Default journal file path (same directory as script)
    script_dir = Path(__file__).parent
    default_journal = script_dir / "Journal_2026.txt"
    
    # Allow custom file path via command line argument
    if len(sys.argv) > 1:
        journal_path = sys.argv[1]
    else:
        journal_path = str(default_journal)
    
    print(f"{Colors.HEADER}{Colors.BOLD}")
    print("╔════════════════════════════════════════════════╗")
    print("║           📋 Task Manager v1.0                 ║")
    print("╚════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}")
    print(f"  Loading: {journal_path}")
    
    # Parse the journal
    tasks_by_date = parse_journal(journal_path)
    
    # Initial display - pending tasks only
    show_done = False
    only_in_progress = False
    display_tasks(tasks_by_date, show_done)
    
    # Interactive loop
    print_help()
    
    while True:
        try:
            command = input(f"\n{Colors.BOLD}>{Colors.RESET} ").strip().lower()
            
            if command in ('q', 'quit', 'exit'):
                print(f"{Colors.DIM}Goodbye!{Colors.RESET}")
                break
            
            elif command in ('a', 'all'):
                show_done = True
                only_in_progress = False
                only_testing = False
                clear_screen()
                display_tasks(tasks_by_date, show_done)
            
            elif command in ('p', 'pending'):
                show_done = False
                only_in_progress = False
                only_testing = False
                clear_screen()
                display_tasks(tasks_by_date, show_done)
            
            elif command in ('s', 'stats'):
                display_stats(tasks_by_date)
            
            elif command in ('r', 'refresh'):
                tasks_by_date = parse_journal(journal_path)
                clear_screen()
                print(f"{Colors.DIM}Refreshed!{Colors.RESET}")
                display_tasks(tasks_by_date, show_done,only_in_progress)
            
            elif command in ('h', 'help', '?'):
                print_help()
            elif command in ('i','progress'):
                clear_screen()
                show_done = False
                only_in_progress = True
                only_testing = False
                display_tasks(tasks_by_date,show_done,only_in_progress)
            elif command in ('t','testing'):
                clear_screen()
                show_done = False
                only_in_progress = False
                only_testing = True
                display_tasks(tasks_by_date,show_done,only_in_progress,only_testing)
            elif command == '':
                continue
            
            else:
                print(f"{Colors.ERROR}Unknown command. Type 'help' for available commands.{Colors.RESET}")
        
        except KeyboardInterrupt:
            print(f"\n{Colors.DIM}Goodbye!{Colors.RESET}")
            break
        except EOFError:
            print(f"\n{Colors.DIM}Goodbye!{Colors.RESET}")
            break


if __name__ == "__main__":
    main()
