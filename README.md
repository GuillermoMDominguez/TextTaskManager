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
python3 task_manager.py --add "Review pull request" --state TODO --due tomorrow --priority high

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
- Design landing page -- IN PROGRESS -- due:10/06/2026 -- priority:HIGH -- recur:weekly -- spent:3h30m
: Mockup approved by stakeholders
+ Header section -- DONE
+ Footer section -- BACKLOG -- due:08/06/2026

## 02/06/2026
- Fix login bug -- DONE -- priority:URGENT -- spent:1h30m
: Root cause was session timeout
- Write unit tests -- BACKLOG #backend #testing -- blockedby:Fix login bug
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
| Time spent | `-- spent:XhYm` | `-- spent:2h30m` |
| Blocked by | `-- blockedby:Title` | `-- blockedby:Fix login bug` |
| Blocks | `-- blocks:Title` | `-- blocks:Write unit tests` |
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
> n Deploy new API --state "IN PROGRESS" --due friday --priority high --recur monthly
> cs 3 done
> md 2 --due +3d --priority urgent
> mv 5 next week
> dup 1 tomorrow
> an 3 Client confirmed requirements
> e 2:n1 Updated note text
> del 4.2
> das 3
> ar 01/06/2026
```

### Natural Language Dates

Anywhere a date is expected, you can use:

| Input | Meaning |
|-------|---------|
| `dd/mm/yyyy` | Explicit date |
| `today` | Today |
| `tomorrow` | Tomorrow |
| `yesterday` | Yesterday |
| `monday` ... `sunday` | Next occurrence of that weekday |
| `mon` ... `sun` | Same (abbreviated) |
| `next week` | +7 days |
| `+3d` | 3 days from now |
| `+2w` | 2 weeks from now |
| `+1m` | 1 month from now |

**Examples:**

```
> n Fix bug --due tomorrow
> mv 3 friday
> n Sprint review --date +2w --due +2w
> md 5 --due +3d
```

### Templates

Save reusable task blueprints with subtasks:

```
> tpl                          # List all templates
> tpl save standup             # Create a new template (interactive)
> tpl standup                  # Create task from template
> tpl del standup              # Delete a template
```

Templates are stored in `.ttm_config` and include: title, state, priority, recurrence, and subtasks.

**Example workflow:**

```
> tpl save deploy
  Template title: Deploy to production
  State [BACKLOG]: IN PROGRESS
  Priority (optional): high
  Subtasks (comma-separated): Run tests, Build Docker image, Push to registry, Notify team

> tpl deploy                   # Creates the full task with 4 subtasks
```

### Time Tracking

Log time spent on tasks:

```
> tt 3 2h                      # Log 2 hours to task 3
> tt 3 30m                     # Log 30 minutes (cumulative)
> tt 3 1h30m                   # Log 1h30m
> tt 3 start                   # Start a timer
> tt 3 stop                    # Stop timer and log elapsed time
```

Time is stored as `-- spent:XhYm` metadata in the journal file. Multiple logs accumulate.

### Task Dependencies

Mark tasks as blocked by other tasks:

```
> block 3 5                    # Task 3 is blocked by task 5
```

This writes `-- blockedby:Title` to the blocked task and `-- blocks:Title` to the blocker, using the actual task title so the file stays human-readable.

### Pomodoro Timer

Built-in focus timer with automatic time logging:

```
> pom                          # Start 25min pomodoro (no task)
> pom 3                        # Start 25min pomodoro for task 3
> pom 3 45                     # Start 45min pomodoro for task 3
```

- Shows countdown in terminal
- Press Enter or Ctrl+C to stop early
- Automatically logs elapsed time to the task

### Burndown Chart

ASCII burndown chart showing progress over time:

```
> bd                           # 14-day burndown (default)
> bd 7                         # 7-day burndown
> bd 30                        # 30-day burndown
```

Output:

```
Burndown (14 days) — 24 total tasks
──────────────────────────────────────
 24 │██████████████
 20 │██████████████
 16 │  ████████████
 12 │      ████████
  8 │          ████
  4 │            ██
  0 │
    └──────────────
     20/05     27/05     03/06

  Ideal: -1.7/day | Current remaining: 8 | Velocity: 16 done
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
| `cal [week] [date]` | Calendar view (month or week) |
| `kb` | Kanban board view |
| `pj [#tag]` | Project/tag view |
| `wr [days]` | Weekly report |
| `bd [days]` | Burndown chart |

**Examples:**

```
> ag 14          # Agenda for next 14 days
> cal            # Calendar for current month
> cal week       # Calendar for current week
> cal 06/2026    # Calendar for June 2026
> cal week 15/06/2026  # Week containing that date
> kb             # Kanban board
> pj             # List all tags with counts
> pj #backend   # Show all tasks tagged #backend
> wr 30         # Monthly report
> bd 7           # Week burndown
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
| `show log` / `hide log` | Toggle system log bar |
| `clear log` | Clear log messages |
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
| `--due DATE` | | Due date (dd/mm/yyyy or natural language) |
| `--priority LEVEL` | `-p` | Priority for quick-add |
| `--recur FREQ` | | Recurrence for quick-add |
| `--date DATE` | `-d` | Target date section (dd/mm/yyyy or natural language) |
| `--check` | | Run integrity check and exit |
| `--fix` | | Run integrity check with auto-fix and exit |
| `--web` | | Launch web UI in background alongside terminal interface |

**Examples:**

```bash
# Add a high-priority task due next Friday
python3 task_manager.py --add "Release v2.0" --priority urgent --due friday

# Add a recurring daily standup
python3 task_manager.py --add "Daily standup" --recur daily --state "IN PROGRESS"

# Add task for next week
python3 task_manager.py --add "Sprint planning" --date "+1w" --due "+1w"

# Check journal for problems
python3 task_manager.py --check

# Auto-fix and show what was repaired
python3 task_manager.py --fix

# Launch both web interface and terminal CLI
python3 task_manager.py --web
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
> n Review metrics --recur weekly --due friday
> cs 3 done   # Automatically creates next instance for next friday
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

On first run, a `.ttm_config` JSON file is created with sensible defaults. Edit it to customize states, priorities, kanban columns, templates, and more.

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
  "date_format": "%d/%m/%Y",
  "templates": {
    "standup": {
      "title": "Daily standup",
      "state": "IN PROGRESS",
      "recurrence": "daily",
      "subtasks": ["Review yesterday", "Plan today", "Blockers"]
    },
    "deploy": {
      "title": "Deploy to production",
      "state": "IN PROGRESS",
      "priority": "HIGH",
      "subtasks": ["Run tests", "Build image", "Push to registry", "Notify team"]
    }
  }
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

## System Log

A single-line status bar at the very bottom of the terminal shows the most recent system message (sync events, errors, warnings). It updates with each new event — only the latest message is shown.

```
 14:35:12 [sync] Pushed successfully
```

| Command | Description |
|---------|-------------|
| `show log` | Show the log bar |
| `hide log` | Hide the log bar |
| `clear log` | Clear all log messages |

Visibility can also be set in `.ttm_config`:

```json
{
  "show_log": true
}
```

---

## Journal Sync

Optionally sync your journal to a private git repository for backup and multi-machine access. When configured, changes are automatically pushed in the background after each save.

### Setup

Run the guided configuration wizard inside the app:

```
> config sync
```

The wizard will:
1. Ask for your git provider (GitHub, GitLab, or custom remote URL)
2. Request a personal access token and username
3. Create a private repo automatically (or use an existing one)
4. Perform the initial sync

If the remote already has a journal backup (e.g., configuring a second machine), the wizard detects it and offers:

| Option | Behavior |
|--------|----------|
| **D (Download)** | Replace local with remote journal (default for new machines) |
| **M (Merge)** | Keep both — rebase local changes on top of remote |
| **P (Push)** | Overwrite remote with local content |

### Commands

| Command | Description |
|---------|-------------|
| `config sync` | Guided sync setup |
| `sync` | Force push now |
| `sync status` | Show sync state |

### How It Works

- **On startup**: pulls remote changes (prompts on conflict)
- **After each save**: debounced push in background (5s delay to batch rapid edits)
- **On exit**: flushes any pending push
- **No network**: fails silently, journal works offline, retries on next change

### Configuration

Sync settings live in `.ttm_config`:

```json
{
  "sync": {
    "enabled": true,
    "remote": "git@github.com:youruser/ttm-journal.git",
    "branch": "main"
  }
}
```

If using HTTPS with a token, credentials are stored separately in `.ttm_secrets` (git-ignored):

```json
{
  "sync_token": "ghp_xxxxxxxxxxxx"
}
```

SSH remotes (`git@...`) require no token — they use your system SSH keys.

### Requirements

- `git` CLI installed (used via subprocess)
- For GitHub: token with `repo` scope
- For GitLab: token with `api` scope

---

## Web Interface

TextTaskManager includes a modern web UI that provides a graphical alternative to the CLI.

### Starting the Web Server

```bash
python3 -m src.tm_web.server           # Default port 5000
python3 -m src.tm_web.server --port 8080  # Custom port
```

Or from within the CLI:

```
> web                    # Start web server on default port
> web 8080               # Start on custom port
```

### Features

The web interface provides:

| View | Description |
|------|-------------|
| **Tasks** | Main task list with filters (All, Pending, In Progress, Done) |
| **Kanban** | Drag-and-drop board with customizable columns |
| **Agenda** | Tasks grouped by due date (Overdue, Today, Soon) |
| **Calendar** | Month/week view with drag-and-drop to reschedule tasks |
| **Stats** | Visual statistics with charts |
| **Weekly Report** | Summary of completed work |
| **Burndown** | Progress chart over time |
| **Tags** | Tag cloud with task counts, click to filter |
| **Time Tracking** | Log time spent on tasks |
| **Pomodoro** | Built-in focus timer |
| **Blockers** | Manage task dependencies |
| **Jira** | View and manage linked Jira issues |
| **Sync** | Monitor git sync status, view history, force sync |
| **Config** | Configure settings, Jira, sync, and more |
| **Log** | View application log |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `n` | New task |
| `t` | Tasks view |
| `k` | Kanban view |
| `a` | Agenda view |
| `d` | Calendar view |
| `s` | Stats view |
| `w` | Weekly report |
| `b` | Burndown chart |
| `g` | Tags view |
| `i` | Time tracking |
| `p` | Pomodoro |
| `x` | Blockers |
| `j` | Jira view |
| `u` | Sync view |
| `c` | Config |
| `l` | Log |
| `r` | Refresh |
| `/` | Focus search |
| `Escape` | Close modal |

**Calendar view shortcuts** (when in calendar view):

| Key | Action |
|-----|--------|
| `[` or `h` | Previous month/week |
| `]` or `l` | Next month/week |
| `m` | Switch to month view |
| `y` | Switch to week view |

### UI Components

The web interface uses a single-page architecture with:

- **State badges**: Click on any task's state to change it via dropdown
- **Task search**: Autocomplete search in the header (press `/`)
- **Blocker assignment**: Text search with autocomplete instead of dropdowns
- **Modals**: For task creation/editing, time logging, etc.
- **Dark/Light themes**: Toggle via the moon/sun icon

### Architecture

```
src/tm_web/
    ├── server.py            ← Flask-like HTTP server (no dependencies)
    │   ├── GET /api/tasks   ← List tasks with filters
    │   ├── POST /api/tasks  ← Create task
    │   ├── PUT /api/tasks/<id>  ← Update task
    │   ├── DELETE /api/tasks/<id>  ← Delete task
    │   ├── GET /api/agenda  ← Agenda data
    │   ├── GET /api/calendar  ← Calendar data (week/month)
    │   ├── GET /api/kanban  ← Kanban data
    │   ├── GET /api/stats   ← Statistics
    │   ├── GET /api/blockers  ← Blocker relationships
    │   ├── POST /api/blockers/add  ← Add blocker
    │   └── ... (more endpoints)
    │
    └── static/
        └── index.html       ← Single-file SPA (HTML + CSS + JS)
```

The web server uses Python's built-in `http.server` module — no Flask or other frameworks required. The frontend is a single HTML file with embedded CSS and vanilla JavaScript.

### Quick Launch (No Terminal Knowledge Required)

Launcher scripts are provided that open **both interfaces** with a double-click — the web UI in your browser and the terminal CLI in the same window:

| Platform | File | How to use |
|----------|------|------------|
| **macOS** | `TextTaskManager.command` | Double-click in Finder |
| **Windows** | `TextTaskManager.bat` | Double-click in Explorer |
| **Linux** | `TextTaskManager.sh` | Double-click (if enabled) or run `./TextTaskManager.sh` |

The launcher will:
1. Check that Python is installed
2. Start the web server in the background
3. Open the web interface in your default browser
4. Show the terminal CLI in the same window

Both interfaces share the same journal — changes in one are immediately visible in the other (after refresh).

To exit, type `q` in the terminal. This stops both the CLI and the web server.

**Linux note:** Some file managers require you to enable "Run executable text files" in preferences, or right-click → "Run as Program". Alternatively, run from terminal: `./TextTaskManager.sh`

---

## Architecture

Modular, no circular dependencies. Command dispatch is split into domain sub-modules:

```
task_manager.py              ← Entry point, prompt loop, crash handling
    │
    ├── src/tm_commands.py   ← Thin router: dispatches to sub-modules, re-exports
    │       ├── tm_cmd_common.py    ← Shared: ViewState, CommandContext, utilities
    │       ├── tm_cmd_crud.py      ← new, cs, edit, delete, move, sub, das, dup
    │       ├── tm_cmd_views.py     ← pending, all, stats, agenda, kanban, find, sort, undo
    │       ├── tm_cmd_features.py  ← template, recurrence, time, block, pomodoro, email
    │       └── tm_cmd_system.py    ← config, sync, help, reload
    │
    ├── src/tm_journal.py    ← File I/O: parse/write journal, task CRUD on disk
    ├── src/tm_logic.py      ← Pure logic: find_task_by_id, date parsing, normalization
    ├── src/tm_features.py   ← Extended: export/import, kanban, weekly report, pomodoro
    ├── src/tm_models.py     ← Data classes: Task, Subtask
    ├── src/tm_config.py     ← VALID_STATES, VALID_PRIORITIES, loaded from .ttm_config
    ├── src/tm_settings.py   ← Settings + secrets I/O (chmod 600, atomic writes)
    ├── src/tm_ui.py         ← Terminal rendering: colors, table layout, display_tasks
    ├── src/tm_form.py       ← Interactive forms (TextField, SelectField, ListPicker)
    ├── src/tm_log.py        ← Single-line status bar (toast pattern)
    ├── src/tm_sync.py       ← Git sync: push/pull/config wizard (optional)
    ├── src/tm_jira.py       ← Jira Cloud integration (optional, requires `requests`)
    ├── src/tm_email.py      ← Email: SMTP or mailto fallback
    ├── src/tm_integrity.py  ← Journal linting and auto-fix
    │
    └── src/tm_web/          ← Web interface (optional)
        ├── server.py        ← HTTP server with REST API
        └── static/index.html  ← Single-page app (no build step)
```

### Design Principles

- **Zero external dependencies** — pure Python standard library (except optional `requests` for Jira)
- **Manual editing compatible** — the file format is designed to be readable and editable by humans
- **Terminal-adaptive UI** — all separators and columns scale to terminal width
- **Secrets isolated** — tokens/passwords in `.ttm_secrets` (gitignored, mode 600), never in `.git/config`
- **Crash-safe** — undo snapshots before every write, atomic file operations where possible
- **Offline-first sync** — git-based, fails silently with no network, retries on next change
- **No build step** — web interface is vanilla HTML/CSS/JS, no npm or bundlers

---

## Jira Integration

Optional module for querying and managing Jira Cloud issues directly from TTM. Requires `pip install requests`.

### Setup

```
> config jira
```

The wizard asks for:
1. Jira Cloud URL (e.g., `https://yourteam.atlassian.net`)
2. Your Atlassian email
3. An API token (generated at https://id.atlassian.com/manage-profile/security/api-tokens)

Credentials are stored in `.ttm_secrets` (gitignored, chmod 600).

### Commands

All Jira commands are prefixed with `j` or `jira`:

| Command | Alias | Description |
|---------|-------|-------------|
| `j` / `j active` | `j a` | My active issues (not Done/Cancelled) |
| `j todo` | `j t` | My issues in "To Do" |
| `j progress` | `j p` | My issues "In Progress" |
| `j done` | `j d` | My completed issues |
| `j review` | `j rv` | My issues "In Review" |
| `j blocked` | `j bk` | My blocked issues |
| `j overdue` | `j od` | My overdue issues |
| `j find <text>` | `j f <text>` | Search issues by text |
| `j open <KEY\|id>` | `j o` | Open issue in browser (Jira key or local task id) |
| `j move <KEY>` | `j mv` | Transition issue status |
| `j notify` | `j n` | Show unread comments mentioning you |
| `j mark` | `j m` | Mark all notifications as read |
| `j link <id> <KEY>` | `j l` | Link local task to Jira issue |
| `j unlink <id>` | `j ul` | Remove Jira link from local task |
| `j import <KEY> [date]` | `j i` | Import Jira issue as local task |

### Examples

```
> j                      # Show my active issues
> j f login bug          # Search for "login bug"
> j o PROJ-123           # Open in browser
> j mv PROJ-123 done     # Transition to Done
> j n                    # Check for new @mentions
> j m                    # Mark all as read
```

### Notifications

`j notify` surfaces comments where you are @mentioned or where you are the reporter. It tracks read state in `.jira_last_seen` and only shows new comments since last check. Run `j mark` to acknowledge them.

### Jira ↔ Local Task Linking

You can link local tasks to Jira issues for quick reference:

```
> j link 3 BD-123        # Link local task #3 to Jira issue BD-123
> j unlink 3             # Remove the link
> j open 3               # Opens BD-123 in browser (follows the link)
> j import BD-123        # Create a local task from a Jira issue (one-shot)
> j import BD-123 2025-06-15   # Import into a specific date section
```

Linked tasks show their Jira key in the task list: `[BD-123]`. The link is stored as metadata (`-- jira:BD-123`) in the journal file.

This is **reference-only** — no bidirectional sync. Changing the local task state does not update Jira, and vice versa. Use `j move BD-123` to change Jira status separately.

### Architecture Note

Local tasks live in journal files; Jira issues live on your Atlassian instance. The linking feature provides a lightweight reference between the two without coupling their lifecycles.

---

## License

MIT
