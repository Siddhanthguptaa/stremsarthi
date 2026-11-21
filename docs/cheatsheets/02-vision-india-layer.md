# Member 2: Vision and the India Layer

**You own:** faces + allowlist, Aadhaar/PAN, UPI QR, number plates, signboards in Indic scripts, PIN/phone/UPI/email text, OTP screens, BGMI/Free Fire IDs, tracking, MaskPlanner, SPECIMEN datasets, video red team.

## Your demo moments
1. SPECIMEN Aadhaar card moving across the frame, masked throughout.
2. Enrolled face stays visible, stranger is blurred.
3. UPI QR and OTP phone screen masked; then the video red-team report.

## Numbers (say "synthetic" every time)
- Clean+light: faces masked 1.0, Aadhaar/PAN/UPI QR/signboard/phone/plates ~1.0; PIN 0.91, OTP 0.92
- Allowlisted face visible: clips ~0.97 settled, stills ~0.92; match threshold 0.40, worst stranger 0.389
- Heavy degradation (blur + dark + compression): below target (plates ~5%, OTP 0-31%, game IDs ~5%), reported separately
- Fast lane ~16 ms p95; accurate lane ~450 ms (at its deadline)
- Red team: card 40% occluded fixed (0 leaks); edge-held card fixed; QR entering 94% -> 68% leak

## Questions you answer
1. **Aadhaar without training?** OCR + Verhoeff checksum + card shape; SPECIMEN-form numbers accepted for tests.
2. **Why no flicker?** Detect every few frames, motion-project, ByteTrack, hysteresis hold.
3. **Why OCR-first plates?** YOLO plate weights are AGPL; we use plate-format matching.
4. **Why YuNet + SFace?** Small, permissive, CPU-capable; InsightFace is non-commercial.
5. **Blurred/dark frames?** Detectors read nothing; we report it separately and use mask-when-unsure sparingly (over-masking makes a stream unusable).
6. **Stranger treated as enrolled?** Per-track voting, and a false allow is treated as worse than a missed allow.
7. **Indic text?** Multi-script recognisers; signboards use a mask-all-text rule.
8. **What did the red team find?** Partial-occlusion leak (fixed), edge entry (partly fixed), pan blur (open).

## Volunteer these
- All data is synthetic; clean/light numbers are optimistic.
- Heavy-level recall low; fast pan blur and a card 60% hidden from the top still leak.
- Signboard mask-all makes text-heavy screens heavily masked (keep it off for gaming/screens).
- Game-ID templates came from our own overlays, not real BGMI/Free Fire: need phone screenshots.
- Eval faces are NASA public-domain portraits; threshold needs re-tuning on real footage.

## If pushed
- "Real footage?" -> "Not yet; that is the next step and the biggest accuracy risk."
- "False masks?" -> "0 on synthetic negatives at clean/light; a bordered real side panel could be masked."
- "Why not train a model?" -> "No permissive data; rules + pretrained are auditable; training later on Colab."
