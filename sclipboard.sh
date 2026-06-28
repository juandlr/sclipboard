#!/bin/sh
export PYTHONPATH="/app/share/sclipboard/src:${PYTHONPATH}"
exec python3 /app/share/sclipboard/src/main.py "$@"
