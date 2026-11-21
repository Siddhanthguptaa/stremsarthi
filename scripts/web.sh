#!/usr/bin/env bash
# Run npm for app/web in a WSL-native build directory. Run through scripts/ss.sh:
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi/scripts/ss.sh bash scripts/web.sh <npm args>
#   e.g.  bash scripts/web.sh ci        bash scripts/web.sh run build        bash scripts/web.sh test
# Why: the source lives on the Windows disk, where npm cannot create node_modules symlinks. The sources are
# synced (one way, repo -> build dir) into ~/webbuild and npm runs there. node_modules and .next stay in
# ~/webbuild and are never copied back. Edit files in app/web in the repo, never in ~/webbuild.
set -euo pipefail
ROOT="${SS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BUILD="${SS_WEBBUILD:-$HOME/webbuild}"
mkdir -p "$BUILD"
rsync -rlt --no-perms --no-owner --no-group --delete \
  --exclude=/node_modules --exclude=/.next --exclude=/coverage --exclude=/tsconfig.tsbuildinfo \
  "$ROOT/app/web/" "$BUILD/"
cd "$BUILD"
export NEXT_TELEMETRY_DISABLED=1
if [ "${1:-}" = "ci" ] && [ ! -f package-lock.json ]; then set -- install; fi
npm "$@"
