#!/bin/sh
# Full "Build it. Break it. Fix it." cycle in one command.
cd "$(dirname "$0")"
python3 bugs.py inject
.venv/bin/python tester/ai_tester.py --name 1-before-fix
python3 bugs.py fix
.venv/bin/python tester/ai_tester.py --name 2-after-fix
.venv/bin/python tester/compare.py 1-before-fix 2-after-fix
