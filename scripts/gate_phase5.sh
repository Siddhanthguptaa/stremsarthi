#!/usr/bin/env bash
# Gate 5: strike guard and live captions (docs/PLAN.md section 5 G5, docs/specs/eval.md 5.9, T5.5).
#
# Run inside WSL from anywhere (scripts/ss.sh sets the venv):
#   bash scripts/ss.sh bash scripts/gate_phase5.sh [options]
# Writes eval/reports/<date>-phase5.md (+ .json) and exits non-zero when the gate fails.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF2'
Usage: scripts/gate_phase5.sh [--data DIR] [--out DIR] [--report-dir DIR] [--reference [--defect NAME]]
                              [--seeded-defects] [--plan] [--help]

Gate G5 (PLAN section 5): the strike guard and live captions on a SYNTHETIC set (eval/data_gen/guard): 5 seeded songs
(never real music, audio stays out of git), 20 music clips, 10 music-under-speech clips (0 dB), 20 speech-only clips
(clean, pink and white noise), 40 noisy 5 s fingerprint snippets (10 dB pink noise, half through MP3 128 kbps) plus 10
unrelated and 8 tempo-changed ones, about 250 written profanity utterances (Hinglish in Latin letters and Devanagari,
English, Kannada, Tamil, Telugu, Bengali, spelling variants, harmless look-alikes), and a 24-segment spoken caption
timeline. All CPU, no network, no keys, no GPU (so no gpu lock). The default data directory is under /tmp
(SS_GUARD_DATA overrides it); it is generated when its manifest is missing.

Steps:
  generate       the set when DATA/manifest.json is missing (about 30 s).
  data check     counts, files, SPECIMEN lint.
  music          guard.music.detector: recall >= 90 % on music clips, false positives <= 5 % on speech clips.
  fingerprint    library from the eval's own songs through guard.music.build_library, FingerprintMatcher on noisy 5 s
                 snippets: >= 90 % correct id, no wrong id, unrelated music <= 5 % false matches.
  profanity      guard.risk.ProfanityDetector: Hinglish recall >= 85 %, no false positives on harmless look-alikes,
                 clean false positives <= 3 %, signal within 0.6 s of the word, processing p95 <= 0.5 s.
  risk meter     signals injected into guard.risk.RiskService on a virtual clock: first level change <= 2 s, expected
                 level, 2 Hz publication.
  captions render  engine.captions.CaptionRenderer in the five scripts against an independent raqm reference.
  captions timing  audio.captions.service.CaptionService: every published caption arrives before its display time,
                 at least 75 % of segments are captioned, muted words never appear.
  report         eval/reports/<date>-phase5.md

A component that does not exist yet (module not importable) is reported NOT RUN and the leg exits 2: never a pass.

Options:
  --data DIR         set directory (default /tmp/streamsarthi-guard-data)
  --out DIR          run directory for the leg JSONs (default eval/runs/phase5-<timestamp>)
  --report-dir DIR   where the report goes (default eval/reports)
  --reference        every leg uses a stand-in that is right by construction (validates the harness: must pass);
                     report <date>-phase5-reference.md
  --defect NAME      with --reference, seed one defect: music_blind | fp_blind | slow_meter | no_deva_profanity |
                     late_captions | basic_shaping. The gate must exit 1.
  --seeded-defects   run the reference stand-ins clean (must exit 0) and once per defect (each must fail its leg with exit
                     1); no product code is exercised. Exit 0 only if every defect was caught.
  --plan             print the steps that would run and exit 0 without running anything
  -h, --help         this text

Exit: 0 pass, 1 a criterion failed, 2 environment or setup error or a missing component (nothing trustworthy was
measured for that leg), 3 a required hardware leg was skipped (none here).
EOF2
}

die_usage() { echo "gate_phase5.sh: $1" >&2; echo "try: scripts/gate_phase5.sh --help" >&2; exit 2; }

DATA="${SS_GUARD_DATA:-/tmp/streamsarthi-guard-data}"; OUT=""; REPORT_DIR=""; REF=0; DEFECT=""; PLAN=0; SEEDED=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --plan) PLAN=1 ;;
    --reference) REF=1 ;;
    --seeded-defects) SEEDED=1 ;;
    --data) [ $# -ge 2 ] || die_usage "--data needs a directory"; DATA="$2"; shift ;;
    --data=*) DATA="${1#*=}" ;;
    --out) [ $# -ge 2 ] || die_usage "--out needs a directory"; OUT="$2"; shift ;;
    --out=*) OUT="${1#*=}" ;;
    --report-dir) [ $# -ge 2 ] || die_usage "--report-dir needs a directory"; REPORT_DIR="$2"; shift ;;
    --defect) [ $# -ge 2 ] || die_usage "--defect needs a name"; DEFECT="$2"; shift ;;
    --defect=*) DEFECT="${1#*=}" ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done
DEFECTS="music_blind fp_blind slow_meter no_deva_profanity late_captions basic_shaping"
case " $DEFECTS " in *" $DEFECT "*) ;; *) [ -z "$DEFECT" ] || die_usage "--defect must be one of: $DEFECTS" ;; esac
[ -z "$DEFECT" ] || [ "$REF" = 1 ] || die_usage "--defect needs --reference"

[ -n "$OUT" ] || OUT="eval/runs/phase5-$(date +%Y%m%d-%H%M%S)"
PHASE_NAME=phase5
[ "$REF" = 0 ] || PHASE_NAME="${PHASE_NAME}-reference"
[ -z "$DEFECT" ] || PHASE_NAME="${PHASE_NAME}-defect-${DEFECT}"
PY=(python -m eval.phase5 --data "$DATA" --out "$OUT")
RF=()
[ "$REF" = 0 ] || RF+=(--reference)
[ -z "$DEFECT" ] || RF+=(--defect "$DEFECT")

step() {
  if [ "$PLAN" = 1 ]; then
    [ "${1:-}" != "--ok-rc" ] || shift 2
    local name="$1"; shift
    echo "PLAN: [$name] $*"
  else
    gate_step "$@"
  fi
}

# defect_caught LEG DEFECT: succeeds when the reference stand-in with that defect makes the leg exit exactly 1
defect_caught() {
  local rc=0
  python -m eval.phase5 --leg "$1" --data "$DATA" --out "$OUT/defect-$2" --reference --defect "$2" >/dev/null 2>&1 || rc=$?
  [ "$rc" = 1 ]
}
# reference_clean LEG: the clean stand-in must pass the leg
reference_clean() { python -m eval.phase5 --leg "$1" --data "$DATA" --out "$OUT/clean" --reference >/dev/null 2>&1; }

GATE_REPORT_HOOK=phase5_report
phase5_report() {
  local extra=()
  [ -z "$REPORT_DIR" ] || extra+=(--report-dir "$REPORT_DIR")
  python -m eval.phase5 --leg report --out "$OUT" --results "$1" --phase-name "$PHASE_NAME" "${extra[@]}"
}

if [ "$PLAN" = 0 ]; then
  command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  mkdir -p "$OUT"
  gate_init "$PHASE_NAME"
fi

# 1. the set
if [ "$PLAN" = 1 ]; then
  echo "PLAN: [generate guard set] (only when $DATA/manifest.json is missing) python -m eval.data_gen.guard --out $DATA"
elif [ ! -f "$DATA/manifest.json" ]; then
  step "generate guard set" python -m eval.data_gen.guard --out "$DATA"
else
  gate_record "generate guard set" 0 "set already present"
fi
step "data check" "${PY[@]}" --leg data
if [ "$PLAN" = 0 ] && [ "${GATE_MAPPED[${#GATE_MAPPED[@]}-1]}" != 0 ]; then
  echo "guard set missing or invalid: no leg is run (nothing was measured)"
  gate_finish
fi

# 2. seeded-defect self-check of the harness (reference stand-ins only)
if [ "$SEEDED" = 1 ]; then
  for leg in music fingerprint profanity meter captions_render captions_timing; do
    step "reference passes: $leg" reference_clean "$leg"
  done
  for pair in music_blind:music fp_blind:fingerprint slow_meter:meter no_deva_profanity:profanity late_captions:captions_timing basic_shaping:captions_render; do
    step "defect caught: ${pair%%:*}" defect_caught "${pair##*:}" "${pair%%:*}"
  done
  if [ "$PLAN" = 1 ]; then exit 0; fi
  GATE_NO_REPORT=1
  gate_finish
fi

# 3. legs
step "music detection" "${PY[@]}" --leg music "${RF[@]}"
step "fingerprint" "${PY[@]}" --leg fingerprint "${RF[@]}"
step "profanity" "${PY[@]}" --leg profanity "${RF[@]}"
step "risk meter" "${PY[@]}" --leg meter "${RF[@]}"
step "captions render" "${PY[@]}" --leg captions_render "${RF[@]}"
step "captions timing" "${PY[@]}" --leg captions_timing "${RF[@]}"

if [ "$PLAN" = 1 ]; then
  echo "PLAN: report -> eval/reports/<date>-${PHASE_NAME}.md (data=$DATA, out=$OUT)"
  exit 0
fi
gate_finish
