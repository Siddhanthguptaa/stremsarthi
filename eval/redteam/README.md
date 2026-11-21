# eval/redteam: audio and chat red team (T7.2)

Adversarial cases pushed through the real `audio.AudioPipeline` + `engine.audio_mute.MuteBook` and `chat.main.build`.
Findings go to the audio and chat engineers (T7.5); this package only measures. Never real personal data: texts and
numbers are SPECIMEN or synthetic, and reports list case ids and technique names (message text: `--show ID`).

    bash scripts/ss.sh python -m eval.run redteam-chat  [--per-label 12] [--category C] [--record-only]
    bash scripts/ss.sh python -m eval.run redteam-audio [--per-technique 2] [--category C] [--technique T] [--record-only]
    bash scripts/ss.sh bash scripts/gate_redteam_audiochat.sh [--quick] [--record-only] [--reference [--defect NAME]]
    bash scripts/ss.sh python -m pytest eval/tests/test_redteam_audiochat.py -q

Reports: `eval/reports/<date>-redteam-chat.md|json`, `-redteam-audio.md|json`, `-redteam-audiochat.md` (gate steps).
Exit: 0 pass, 1 criterion failed, 2 environment error. `--record-only` fails only on hard invariants (crash, text in logs,
`!hold` from non-staff, replay leak, ...), not on the miss-rate ceilings. Ceilings are initial targets (a change needs a
`docs/DECISIONS.md` entry): `chat/attacks.py` CEILINGS, `audio/run.py` CEILING and CEILING_FAILSAFE.

Layout: `chat/attacks.py` (transforms, bases, categories), `chat/run.py` (evasion, injection with a worst-case obedient
LLM stand-in, `!hold` spoofing, replayed ids, 4,000 message flood, malformed and huge inputs, log scan),
`audio/run.py` (TTS-composed clips per technique, failure injection, scoring from the output audio), `cli.py`.

Seeded defects (`--reference --defect NAME`, stand-in modules right by construction; the run must exit 1):
chat `bypass_all`, `leak_logs`, `hold_nonstaff`; audio `late_spans`, `no_failsafe` (eval/phase3.py reference build).

Limits: the recogniser is the phase-3 oracle and the Groq stage is a stand-in; the TTS is English flite (see
eval/data_gen/audio/suite.py LIMITATIONS). The GPU is not used (no flock needed).
