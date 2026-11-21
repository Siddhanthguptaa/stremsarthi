# Member 3: Audio, Captions and Guard

**You own:** voice activity detection, the STT race (Sarvam, Groq Whisper, local faster-whisper), spoken-PII rules (Hindi/Kannada/Tamil/Telugu/Bengali number words), mute spans, bleep, mouth blur, live captions and the caption renderer, music guard (YAMNet + fingerprint), profanity, risk meter.

## Your demo moments
1. A spoken fake phone number muted with a bleep and mouth blur (only if the reel audio works live; otherwise show the gate numbers).
2. A caption on screen with muted words shown as the masked block characters.
3. Music detector and risk meter warning.

## Numbers (say "oracle recogniser, English TTS")
- PII mute coverage 95.9%; digit coverage 98.8%; over-mute 14.4% (limit 15%); pipeline p95 2.49 s
- API down: PII muted 100%; local recogniser 91.6%; mouth blur 98.3%
- Music: recall 0.93, fingerprint 40/40 correct, 0 false matches; music "on" latency p95 4.5 s vs 2 s spec (say it)
- Hinglish profanity recall 0.97; meter reacts in 0 s; captions 0 of 23 late, 0 muted words shown
- Red team: STT failure leak 2.5%; empty-transcript leak fixed (was 100%)

## Questions you answer
1. **How do you mute before it airs?** The 4 s window: STT, rules, then sample-accurate mute spans that are ready before the samples leave.
2. **STT API down or slow?** Local fallback, then fail-safe mutes unverified voiced speech.
3. **Hindi number words?** Number-word tables for five languages + script digits; three or more digits heard one by one are muted at once.
4. **Why bleep + mouth blur?** Stops lip-reading while muted.
5. **Can captions leak muted PII?** No: muted words are redacted before translation and never sent; a review found a leak path and it was fixed and tested.
6. **Why Chromaprint + YAMNet?** Permissive, local, nothing sent out.
7. **Why a delay for captions?** Same window: caption is shown with the audio it matches.
8. **Prompt/LLM risk in audio?** Rules decide; Groq only sees ambiguous spans, nothing logged.

## Volunteer these
- No real-speech test: the audio gate uses an oracle recogniser and English TTS for every language.
- Spelled-out UPI IDs are the weakest (late spans); sped-up speech isn't detected by the VAD.
- IndicTrans2 needs a Hugging Face token; captions run on Sarvam only for now.
- Spec deviations we chose: 0.25 s fail-safe pad, early muting of digit runs (hard-negative mute rate 22%).
- Music guard tuned on synthetic songs only.

## If pushed
- "Real accuracy?" -> "Needs real speech and keys; the gate proves timing, VAD, rules and mute application."
- "Over-muting?" -> "14.4% on the gate set; the limit is 15% and we trade a little over-mute for safety."
- "What if the transcript is empty?" -> "Voiced speech with an empty transcript is muted; the red team found that gap."
