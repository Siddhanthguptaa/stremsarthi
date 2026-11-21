# Shared sheet: everyone reads this first

## 30-second hook
Indian streamers leak Aadhaar cards, UPI QR codes, OTP pop-ups, number plates and phone numbers on live streams, and clips last forever. **Stream Sarthi sits between the streamer and YouTube/Twitch and removes private information from video, audio and chat before viewers see it, with a few seconds of delay.**

## The one idea to repeat
**Delay and verify.** Buffer 4 seconds, check every frame, and anything not yet checked goes out masked. Safety comes from the architecture (fail-safe), not from the detectors being perfect.

## Architecture in one breath
OBS or phone (SRT/RTMP) -> MediaMTX ingest -> **Engine**: 4 s delay buffer, vision lanes (fast + accurate) -> tracking -> mask planner, audio (VAD -> STT race -> PII rules -> mute spans), compositor (masks, mouth blur, slate, captions) -> x264/NVENC -> RTMPS to YouTube/Twitch. Chat runs as a separate process (rules + Groq -> moderator queue). Guard (music + risk meter), captions, FastAPI + WebSocket, Next.js apps. Everything talks over a typed event bus (ZeroMQ). Video never leaves the machine; only audio chunks and chat text go to APIs; nothing is stored.

## Numbers (always say the caveat)
| Fact | Number | Caveat |
|---|---|---|
| Capture-to-output delay p95 | 4.53 s (limit 5 s) | idle laptop, CPU path, synthetic stream |
| Output fps | 30.3 | same |
| Card leaks in main run | 0 of 2,004 visible frames | synthetic SPECIMEN card |
| Fail-safe (vision lane killed) | 0 card frames out, 985 slate frames | injected fault |
| Hold | slate within 1 frame, audio -120 dBFS, live again ~4.2 s | quiet machine |
| End-to-end smoke | 36 of 36 checks, no keys | rehearsal session, synthetic |
| Detectors at clean+light | ~1.0 on most categories | SYNTHETIC; heavy-degradation level is below target and reported separately |
| Chat, blind set, rules only | abuse 82%, scam 96%, fake UPI 81%, doxxing 89%, clean false positives 1.9% | no Groq key; own set scored 100%, blind set is the honest figure |
| Audio PII mute coverage | 95.9% | ORACLE recogniser + English TTS, not real recognition |
| Cost | about Rs 10 to 63 per hour (captions off) | assumed rates, nothing live-tested |

## Say these before they ask
- All accuracy numbers are synthetic or oracle-based. Nothing is verified live yet.
- Heavy blur/dark/compression recall is below target; we report it separately.
- Abuse and doxxing need the Groq stage; rules alone are below target.
- Not tested on a real phone, YouTube/Twitch, or the RTX 5050 demo laptop.

## Handoff rule
Never say "not my part". Say: "**<Name> owns that. Short answer: ...**", then hand over.
Routing: delay/fail-safe/hold -> Member 1. Detectors/accuracy -> Member 2. Speech/captions/music -> Member 3. Chat/product/reports/cost/legal -> Member 4.

## Demo order (5 min)
0:00 M4 hook -> 0:30 M1 architecture + fail-safe idea -> 1:15 M2 masked video -> 2:30 M3 audio + captions -> 3:30 M4 chat, moderator console, report -> 4:15 M1 hold button -> 4:40 M4 evidence, weaknesses, next steps.

## Rules of the room
Use the word **synthetic** next to every accuracy figure. Don't demo anything marked "not verified" in `docs/STATUS.md`. Close every other app on the demo laptop: the engine needs the CPU, and under overload it correctly shows the privacy slate.
