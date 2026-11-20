"""T3.1 spoken-PII audio suite: TTS-composed clips with ground-truth word times by construction.

Every word is synthesised on its own and the clip is the concatenation, so the time of each word is the
position it was placed at (no forced alignment, no recogniser in the loop). All spoken PII is SPECIMEN
data from ``eval.data_gen.common.specimen``. See ``suite.py`` for the manifest format and the limits of the
offline TTS (only English flite voices exist offline: regional number words are spoken with an English
phonemiser, so they are intelligible as sound events, not as native pronunciation).
"""

GENERATOR_VERSION = "audio-1"
