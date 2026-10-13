#!/bin/bash
# Mbarara Auction System - run ./start.sh to set up and start everything.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3.10+ first."
    echo "  macOS: brew install python@3.12"
    echo "  Linux: sudo apt install python3 python3-venv"
    exit 1
fi

exec python3 start.py "$@"
