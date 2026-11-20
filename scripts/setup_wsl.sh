#!/usr/bin/env bash
# Idempotent, user-level project setup. Never runs sudo (it prints what you must run yourself).
#   scripts/setup_wsl.sh [--cpu-only] [--extras torch,indic-ocr,agpl]
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/node/bin:$PATH"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CPU_ONLY=0
EXTRAS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --cpu-only) CPU_ONLY=1 ;;
    --extras) EXTRAS="${2:-}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n== %s\n' "$*"; }

say "System packages you may need to install yourself (needs sudo, not run here)"
missing=()
ldconfig -p | grep -q libraqm || missing+=(libraqm0)
command -v espeak-ng >/dev/null 2>&1 || missing+=(espeak-ng)
command -v fpcalc >/dev/null 2>&1 || missing+=(libchromaprint-tools)
if [ "${#missing[@]}" -gt 0 ]; then
  echo "sudo apt update && sudo apt install -y ${missing[*]}"
else
  echo "none missing"
fi

say "uv and Python 3.11"
command -v uv >/dev/null 2>&1 || { curl -LsSf https://astral.sh/uv/install.sh | sh; }
uv python install 3.11
VENV="${SS_VENV:-$HOME/.venvs/stremsarthi}"
mkdir -p "$(dirname "$VENV")"
[ -d "$VENV" ] || uv venv --python 3.11 "$VENV"
export VIRTUAL_ENV="$VENV"
export PATH="$VIRTUAL_ENV/bin:$PATH"

say "Python packages"
uv pip install -r requirements/base.txt -r requirements/dev.txt
# rapidocr pulls opencv-python-headless; keep exactly one OpenCV distribution (docs/DECISIONS.md D-34)
uv pip uninstall opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless >/dev/null 2>&1 || true
uv pip install opencv-contrib-python-headless==4.12.0.88
if [ "$CPU_ONLY" = 0 ] && nvidia-smi >/dev/null 2>&1; then
  uv pip uninstall onnxruntime 2>/dev/null || true
  uv pip install -r requirements/gpu.txt
else
  uv pip install -r requirements/ort-cpu.txt
fi
uv pip install --no-deps -r requirements/nodeps.txt
case ",$EXTRAS," in
  *,torch,*) uv pip install -r requirements/extras-torch.txt --extra-index-url https://download.pytorch.org/whl/cu128 ;;
esac
case ",$EXTRAS," in
  *,indic-ocr,*) uv pip install easyocr==1.7.2 ;;
esac
case ",$EXTRAS," in
  *,agpl,*) uv pip install -r requirements/extras-agpl.txt ;;
esac
uv pip check || echo "WARNING: pip check reported conflicts (see above)"

say "Tools"
bash scripts/fetch_tools.sh

say ".env"
[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example: fill in your keys (never commit it)"; }

say "Doctor"
python scripts/doctor.py || true
