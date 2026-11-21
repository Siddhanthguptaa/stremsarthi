#!/usr/bin/env bash
# Build everything the judge demo reel needs, into /tmp/streamsarthi-demo (override with DEMO_OUT), and print the
# commands that run it. SPECIMEN data only. No GPU, no network except the one-time NASA portrait fetch (hash-checked).
#
# From Git Bash on Windows:
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/demo_assets.sh
# Options: --no-audio (skip the 35 s TTS build), --help
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

OUT="${DEMO_OUT:-/tmp/streamsarthi-demo}"
AUDIO=1
for a in "$@"; do
  case "$a" in
    --no-audio) AUDIO=0 ;;
    -h | --help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "unknown option: $a" >&2
      exit 2
      ;;
  esac
done
mkdir -p "$OUT"

echo "== 1/4 face portraits (NASA, public domain; fetched once, SHA-256 checked)"
python - <<'PY'
import sys
from pathlib import Path
from eval.data_gen.video import faces
problems = faces.fetch_sources(Path("eval/data/generated/faces/src"))
if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
print("portraits ready")
PY

if [ "$AUDIO" = 1 ]; then
  echo "== 2/4 audio track (flite TTS, 90 s, 48 kHz mono) and its timeline"
  python -m eval.data_gen.demo_audio --out "$OUT"
else
  echo "== 2/4 audio skipped (--no-audio)"
fi

echo "== 3/4 contact sheet of the ten reel scenes (a quick look, not used by the demo)"
DEMO_OUT="$OUT" python - <<'PY'
import os
import cv2
import numpy as np
from eval.sender.scenes import ScenePlayer
from eval.sender.demo_scenes import REEL, DEMO_REEL_SCRIPT
player = ScenePlayer(DEMO_REEL_SCRIPT, 30)
tiles, t0 = [], 0.0
for name, dur, _ in REEL:
    frame, scene, _t = player.render(int((t0 + dur * 0.6) * 30))
    assert scene == name, (scene, name)
    tiles.append(cv2.resize(frame, (384, 216), interpolation=cv2.INTER_AREA))
    t0 += dur
rows = [np.hstack(tiles[i : i + 5]) for i in range(0, 10, 5)]
path = os.path.join(os.environ["DEMO_OUT"], "demo_reel_contact.png")
cv2.imwrite(path, np.vstack(rows)[..., ::-1])
print("wrote", path)
PY

echo "== 4/4 chat script plan"
python -m eval.demo.chat_feeder --print-plan > "$OUT/chat_plan.json"
echo "wrote $OUT/chat_plan.json"

cat <<EOT

Ready in $OUT:
  demo_reel.wav, demo_reel_timeline.json   audio track and what is said when (two SPECIMEN PII moments, a music bed, a curse)
  demo_reel_contact.png                    one frame per scene
  chat_plan.json                           the scripted chat (times, kinds)

Run it (SS = MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh):

  1. Video + audio source (90 s loop; put the SRT url from the /go-live ingest card, or GET /api/pairing):
       SS python -m eval.sender.sender --url "<SRT url>" --script demo_reel --duration 90 \
          --audio-file $OUT/demo_reel.wav --truth $OUT/demo_reel_truth.jsonl
     For a longer run use --duration 270 (the reel loops three times); the audio loops with it.

  2. Chat (replaces 'python -m chat.main'; start it about 3 s after the sender; the API must run with it):
       API env, before the API starts:   SESSION_CHAT_CMD="python -m eval.demo.chat_feeder --profile {profile}"
       the profile needs chat.enabled: true; the feeder forces YouTube only and a placeholder client id.
       by hand instead (same bus endpoints as the API: BUS_SUB_ENDPOINT / BUS_PUB_ENDPOINT):
         SS python -m eval.demo.chat_feeder --profile demo --start-delay 3 [--rules-only] [--loop]
     Flags: --time-scale N (N times faster), --rules-only (never call Groq), --loop (replay the 90 s script).

  3. Open the streamer app and the moderator console (flagged messages appear in the queue: 2 abuse, 2 scam, 1 doxxing).
     The moderator's !hold lands at 84 s (calm scene), the broadcaster's !unhold at 88 s.

Notes: the sender's optical stamp stays in the top-left corner (256 x 32 px, never masked). A file audio track has no
beeps, so use the default beep sender (no --audio-file) for delay and A/V-offset measurements.
EOT
