# Text Task Manager

Text Task Manager is a CLI application intended to organize tasks by
processing a journal file and offering a view allowing for filtering
and tracking.

The application parses a text file with a defined format and displays
taks ordered by date and with its current status: BACLONG, IN PROGRESS,
WAITING, TESTING, CANCELLED and DONE.

You can use the app one of two ways:

- task_manager.py [path-to-journal-file]
- task_manager.py

If called without arguments it will look for a
text file called _Journal.txt_ in the same directory.

## File Format

The application parses a plain text file line-by-line, all text that
does not match one of the following rules is ignored by the parser:

- __Date__: Date lines have the format "## dd/mm/yyyy" and nothing more on the line.
- __Tasks__: Tasks start with a single '-' character. Whitespace is allowed before this character.

### Task Declaration

On task lines the following format is used to extract task title, state and notes:

- __Title__: The text between the starting character and any delimiter is used as the task title.
- __State__: State is tracked with the '--' delimiter. The following states are allowed: BACKLOG, IN PROGRESS, WAITING, TESTING, DONE are CANCELLED. The current state of a task is determined by the last state found by the parser.
- __Note__: A note can be added to the task using the ':' delimiter.

### Example Task

An example that uses all these features is:

```

## 01/01/1970

- Create a sample task -- IN PROGRESS : This is sample text! -- DONE

```

This line will be parsed as this:

- Title: Create a sample task
- Notes: This is sample text!
- Date: 01/01/1970
- State: __DONE__

All formated text must fit into a line for the parser to recognize it.

## Available options

The following options are available on the application:

- __a/all__: Show all tasks, including done are cancelled.
- __p/pending__: Shows pending tasks (not completed).
- __i/in progress__: Show tasks currently in progress or testing.
- __t/in testing__: Show tasks in testing.
- __s/stats__: Show task statistics window.
- __r/refresh__: Reload and re-parse the journal file.
- __h/help__: Show a help message.
- __q/quit__: Exit the application.

By default, the application opens in __pending__ mode.