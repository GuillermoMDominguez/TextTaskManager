#!/bin/bash
# TextTaskManager - macOS Launcher
# Double-click this file to open both the web interface and the terminal CLI

cd "$(dirname "$0")"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please install Python 3 from https://www.python.org/downloads/"
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Starting TextTaskManager..."
echo "The browser will open with the Web UI."
echo "You can also use the terminal interface below."
echo "Type 'q' to quit (this will also stop the web server)."
echo ""

python3 task_manager.py --web
