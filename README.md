# TextTaskManager

A pure-Python CLI task manager that stores everything in **plain text journal files** you can edit by hand. No databases, no external dependencies — just `.txt` files and a terminal.

```
╔══════════════════════════════════════╗
║       Task Manager v1.2              ║
╚══════════════════════════════════════╝
  Loading: journals/Javier_Journal.txt
```

---

## Quick Start

```bash
# Enter interactive mode
python3 task_manager.py

# Open a specific journal
python3 task_manager.py MyJournal

# Quick-add a task without entering interactive mode
python3 task_manager.py --add "Review pull request" --state TODO --due 10/06/2026 --priority high

# Check journal integrity
python3 task_manager.py --check

# Auto-fix common issues from manual edits
python3 task_manager.py --fix
```

---

## Journal File Format

Journals are plain `.txt` files in the `journals/` directory. You can edit them manually with any text editor.

```
## 03/06/2026
- Design landing page -- IN PROGRESS -- due:10/06/2026 -- priority:HIGH -- recur:weekly
: Mockup approved by stakeholders
+ Header section -- DONE
+ Footer section -- BACKLOG -- due:08/06/2026

## 02/06/2026
- Fix login bug -- DONE -- priority:URGENT
: Root cause was session timeout
- Write unit tests -- BACKLOG #backend #testing
```

### Syntax

| Element | Format | Example |
|---------|--------|---------|
| Date header | `## dd/mm/yyyy` | `## 03/06/2026` |
| Task | `- title -- STATE` | `- Fix bug -- IN PROGRESS` |
| Subtask | `+ title -- STATE` | `+ Write tests -- BACKLOG` |
| Note | `: text` | `: Discussed with team` |
| Due date | `-- due:dd/mm/yyyy` | `-- due:15/06/2026` |
| Priority | `-- priority:LEVEL` | `-- priority:HIGH` |
| Recurrence | `-- recur:FREQ` | `-- recur:weekly` |
| Tags | `#tagname` in text | `- Task #frontend #urgent` |

---

## Interactive Commands

### Task Management

| Command | Description |
|---------|-------------|
| `n [title] [--state X] [--date dd/mm/yyyy] [--due dd/mm/yyyy] [--priority X] [--recur X]` | Create a new task |
| `cs <id> [state]` | Change task/subtask state |
| `e <id> <new text>` | Edit task, subtask, or note text |
| `del <id>` | Delete task, subtask, or note |
| `mv <id> <dd/mm/yyyy>` | Move task to another date section |
| `dup <id> [dd/mm/yyyy]` | Duplicate task with subtasks and notes |
| `an <id> <note>` | Add a note to a task |
| `md <id> [--due ...] [--priority ...] [--tags ...]` | Edit metadata |
| `das <id>` | Mark all subtasks DONE (auto-closes parent) |
| `ar [dd/mm/yyyy]` | Archive finished tasks |
| `u` | Undo last change |

**Examples:**

```
> n Deploy new API --state "IN PROGRESS" --due 15/06/2026 --priority high --recur monthly
> cs 3 done
> md 2 --due 20/06/2026 --priority urgent
> mv 5 10/06/2026
> dup 1 15/06/2026
> an 3 Client confirmed requirements
> e 2:n1 Updated note text
> del 4.2
> das 3
> ar 01/06/2026
```

### Views

| Command | Description |
|---------|-------------|
| `p` / `pending` | Show pending tasks only (default) |
| `a` / `all` | Show all tasks including done |
| `i` / `progress` | Show in-progress and testing tasks |
| `t` / `testing` | Show testing tasks only |
| `s` / `stats` | Show statistics with bar chart |
| `ag [days]` | Agenda: tasks grouped by due date (default 7 days) |
| `kb` | Kanban board view |
| `pj [#tag]` | Project/tag view |
| `wr [days]` | Weekly report |

**Examples:**

```
> ag 14          # Agenda for next 14 days
> kb             # Kanban board
> pj             # List all tags with counts
> pj #backend   # Show all tasks tagged #backend
> wr 30         # Monthly report
```

### Search & Filter

| Command | Description |
|---------|-------------|
| `f <text>` | Free-text search in titles and notes |
| `f #tag` | Filter by tag |
| `f priority:high` | Filter by priority (low/medium/high/urgent/any/none) |
| `f due:overdue` | Filter overdue tasks |
| `f due:today` | Filter tasks due today |
| `f due:week` | Filter tasks due within 7 days |
| `f due:none` | Tasks without due date |
| `f due:dd/mm/yyyy` | Tasks due on specific date |
| `fc` | Clear active filter |

**Examples:**

```
> f #frontend
> f priority:urgent
> f due:overdue
> f login bug
> fc
```

### Sorting

```
> sort priority asc     # Sort by priority (URGENT first)
> sort due_date desc    # Sort by due date (furthest first)
> sort state            # Sort by state (workflow order)
> sort none             # Disable sorting (file order)
```

### Export & Import

```
> export json                    # Export to tasks_export.json
> export csv /tmp/report.csv    # Export to specific path
> export md                      # Export as Markdown report
> import /path/to/tasks.json    # Import tasks from JSON
```

**JSON format:**
```json
[
  {
    "title": "My task",
    "state": "BACKLOG",
    "date": "03/06/2026",
    "due_date": "10/06/2026",
    "priority": "HIGH",
    "tags": ["#backend"],
    "notes": ["Some context"],
    "subtasks": [{"title": "Sub 1", "state": "BACKLOG"}]
  }
]
```

### Other

| Command | Description |
|---------|-------------|
| `ck` | Lint journal: validate format, states, dates, priorities |
| `se [email]` | Email pending tasks |
| `r` | Reload journal from disk |
| `h` / `help` | Show help |
| `q` / `quit` | Exit |

---

## CLI Arguments (Non-Interactive)

```
python3 task_manager.py [journal] [options]
```

| Flag | Short | Description |
|------|-------|-------------|
| `--add "title"` | `-a` | Quick-add task and exit |
| `--state STATE` | `-s` | State for quick-add (default: BACKLOG) |
| `--due dd/mm/yyyy` | | Due date for quick-add |
| `--priority LEVEL` | `-p` | Priority for quick-add |
| `--recur FREQ` | | Recurrence for quick-add |
| `--date dd/mm/yyyy` | `-d` | Target date section for quick-add |
| `--check` | | Run integrity check and exit |
| `--fix` | | Run integrity check with auto-fix and exit |

**Examples:**

```bash
# Add a high-priority task for next week
python3 task_manager.py --add "Release v2.0" --priority urgent --due 10/06/2026

# Add a recurring daily standup
python3 task_manager.py --add "Daily standup" --recur daily --state "IN PROGRESS"

# Check journal for problems
python3 task_manager.py --check

# Auto-fix and show what was repaired
python3 task_manager.py --fix
```

---

## Recurring Tasks

Tasks can repeat automatically. When a recurring task is marked DONE or CANCELLED, a new instance is created for the next occurrence.

| Frequency | Alias | Next date logic |
|-----------|-------|-----------------|
| `daily` | `D` | +1 day |
| `weekly` | `W` | +7 days |
| `biweekly` | `BW` | +14 days |
| `monthly` | `M` | Same day next month |
| `yearly` | `Y` | Same date next year |

```
> n Review metrics --recur weekly --due 10/06/2026
> cs 3 done   # Automatically creates next instance for 17/06/2026
```

---

## Integrity Check & Auto-Fix

The integrity checker validates your journal for common issues from manual editing:

| Check | Auto-fixable |
|-------|:---:|
| Invalid date headers | Yes (if date is parseable) |
| Invalid/unknown states | Yes (case correction) |
| Invalid priorities | No (flagged only) |
| Invalid due dates | No (flagged only) |
| Invalid recurrences | No (flagged only) |
| Orphan subtasks (no parent) | No (flagged only) |
| Orphan notes (no parent) | No (flagged only) |
| Empty subtasks | No (flagged only) |
| Consecutive blank lines | Yes (removed) |
| Trailing whitespace | Yes (trimmed) |
| Unrecognized line format | No (flagged only) |

Auto-fix runs silently every time you open a journal interactively. You can also run it explicitly:

```bash
python3 task_manager.py --check   # Report only
python3 task_manager.py --fix     # Report + repair
```

---

## Kanban Board

The `kb` command renders a columnar kanban view adapted to your terminal width:

```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│   BACKLOG    │ IN PROGRESS  │   TESTING    │     DONE     │
├──────────────┼──────────────┼──────────────┼──────────────┤
│ [1] H Fix DB │ [3] M Deploy │ [5] L Tests  │ [7] Release  │
│ [2] U Hotfix │ [4] Review   │              │              │
├──────────────┼──────────────┼──────────────┼──────────────┤
│      2       │      2       │      1       │      1       │
└──────────────┴──────────────┴──────────────┴──────────────┘
```

Columns are configurable via `kanban_columns` in `.ttm_config`.

---

## Configuration

On first run, a `.ttm_config` JSON file is created with sensible defaults. Edit it to customize states, priorities, kanban columns, and more.

```json
{
  "states": ["BACKLOG", "IN PROGRESS", "WAITING", "TESTING", "DONE", "CANCELLED"],
  "finished_states": ["DONE", "CANCELLED"],
  "priorities": ["LOW", "MEDIUM", "HIGH", "URGENT"],
  "priority_aliases": {"L": "LOW", "M": "MEDIUM", "H": "HIGH", "U": "URGENT"},
  "default_state": "BACKLOG",
  "kanban_columns": ["BACKLOG", "IN PROGRESS", "TESTING", "DONE"],
  "sort_by": "none",
  "sort_direction": "asc",
  "agenda_days": 7,
  "max_undo": 20,
  "date_format": "%d/%m/%Y"
}
```

Config file search order: project directory > cwd > script directory > home directory.

---

## Email Reports

Send pending tasks by email via SMTP or system mail client.

```
> se                          # Send to default recipient
> se user@example.com         # Send to specific address
```

Configure in `.task_manager_email.json` or via environment variables:
- `TM_EMAIL_SMTP_HOST`, `TM_EMAIL_SMTP_PORT`, `TM_EMAIL_SMTP_USER`, `TM_EMAIL_SMTP_PASSWORD`
- `TM_EMAIL_SMTP_SENDER`, `TM_EMAIL_DEFAULT_RECIPIENT`, `TM_EMAIL_SUBJECT_PREFIX`

Falls back to opening system mail client (`mailto:`) if SMTP is not configured.

---

## Task IDs

IDs are **session-only** — assigned at load time and may change after refresh or restart. This is by design: since journals are plain text files meant for manual editing, persistent IDs would break with hand edits.

| Format | Meaning |
|--------|---------|
| `3` | Task #3 |
| `3.2` | Subtask 2 of task 3 |
| `3:n1` | Note 1 of task 3 |

---

## Undo System

Every destructive operation saves a journal snapshot. Undo restores the previous state:

```
> del 5       # Oops!
> u           # Restored.
```

Stack depth: configurable via `max_undo` (default: 20). Session-only.

---

## Architecture

Modular, no circular dependencies:

```
tm_settings → tm_config → tm_models → tm_journal/tm_logic
                                        ↓
                              tm_features/tm_ui/tm_integrity
                                        ↓
                                   tm_commands
                                        ↓
                                  task_manager.py
```

- **Zero external dependencies** — pure Python standard library
- **Manual editing compatible** — the file format is designed to be readable and editable by humans
- **Terminal-adaptive UI** — all separators and columns scale to terminal width

---

## License

MIT
