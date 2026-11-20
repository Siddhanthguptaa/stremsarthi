#!/usr/bin/env bash
# Lead checkpoint: format, lint, full CPU test suite. Run through scripts/ss.sh.
#   scripts/checkpoint.sh [extra dirs to skip are not supported: edit EXCLUDE]
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
DIRS="contracts engine relay vision models audio guard chat app/api eval scripts"
EXISTING=""
for d in $DIRS; do [ -d "$d" ] && EXISTING="$EXISTING $d"; done
echo "dirs:$EXISTING"
ruff format $EXISTING >/dev/null 2>&1
echo "== ruff check"
ruff check $EXISTING 2>&1 | tail -5
echo "== ruff format --check"
ruff format --check $EXISTING 2>&1 | tail -3
echo "== pytest"
TESTS=""
for d in contracts/tests engine relay vision models audio guard chat app/api eval scripts; do [ -d "$d" ] && TESTS="$TESTS $d"; done
python -m pytest $TESTS -q --no-header -p no:cacheprovider 2>&1 | tail -15
