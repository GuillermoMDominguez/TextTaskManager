#!/bin/bash
# TextTaskManager - Linux Launcher
# Run this script or make it executable and double-click to open both interfaces

cd "$(dirname "$0")"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Install it with your package manager:"
    echo "  Ubuntu/Debian: sudo apt install python3"
    echo "  Fedora: sudo dnf install python3"
    echo "  Arch: sudo pacman -S python"
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Starting TextTaskManager..."
echo "The browser will open with the Web UI."
echo "You can also use the terminal interface below."
echo "Type 'q' to quit (this will also stop the web server)."
echo ""

python3 task_manager.py --web
