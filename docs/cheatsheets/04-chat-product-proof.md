# Member 4: Chat, Product and Proof

**You own:** the chat shield (YouTube/Twitch connectors, classifier cascade, moderator queue and console, `!hold`), the FastAPI backend, the Next.js streamer app, the safety report, gates and red-team evidence, cost, the pitch, the close.

## Your demo moments
1. Scripted chat: Hinglish abuse, a fake UPI link and a doxxing attempt hidden from the overlay, queued for the moderator.
2. Safety report: events by category, time and confidence only; no images, no PII.
3. Evidence tabs: the gate report and the red-team report; then the close.

## Numbers
- Blind chat set (rules only, no Groq): abuse 82%, scam 96%, fake UPI 81%, doxxing 89%, clean false positives 1.9%. Our own set scored 100%: say the gap.
- Red team (chat): homoglyph 0%, URL tricks 8%, leetspeak 10%, prompt injection 0%, split words 19% (ceiling 15%: open)
- Smoke: 36 of 36 checks with no keys; report had 109 events, privacy scan clean
- Cost: about Rs 10-63 per hour, captions off; +Rs 26-117 with Sarvam captions (assumed rates)
- YouTube: 3 h plan about 6.75 s poll interval; a ban costs 200 of 2,000 daily units, so timeouts are off by default

## Questions you answer
1. **How does the chat shield work?** Rules decide obvious cases instantly; ambiguous ones go to Groq with a timeout; Groq down means hold for the moderator.
2. **Can a message trick the AI?** Rules-final results are never overridden; instruction-like text that gets an LLM "clean" is held; messages are marked untrusted in the prompt.
3. **Platform actions?** Delete everywhere; timeouts optional (quota cost); staff messages are never hidden.
4. **Who sees what?** Role tokens: the streamer never sees hidden text; moderators can't use streamer controls.
5. **Where is data stored?** Nowhere; the report has categories and times and is validated against phone/email/UPI/Aadhaar/PAN patterns.
6. **How did you verify?** Gates per phase, a blind held-out set, red teams, three independent reviews, a first real end-to-end smoke.
7. **DPDP / platform policy?** Designed for data minimisation and no storage; don't claim legal compliance.
8. **Business model?** Creator tools, networks, esports organisers; edge box per streamer.

## Volunteer these
- Abuse and fake-UPI targets are not met without the Groq key; split-word evasions still at 19%.
- Nothing has run against live YouTube or Twitch; Groq's real rate limits are unconfirmed (docs disagree).
- Profanity-as-banter ("bc kya clutch tha") is held for a moderator by default: a policy choice.
- Browser tests are built but not yet run (Chromium needs two system libraries).
- The demo console is built and shown, but not yet verified stable under load.

## If pushed
- "Is it live?" -> "No. We run end to end locally with synthetic data and say so."
- "Why trust your numbers?" -> "Independent blind set and reviewers; we report the failures."
- "What do you need next?" -> "Groq and Sarvam keys, real phone footage, the demo laptop, live platform test."
