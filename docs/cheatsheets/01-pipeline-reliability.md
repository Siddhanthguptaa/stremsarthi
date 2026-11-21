# Member 1: Pipeline and Reliability

**You own:** MediaMTX ingest (SRT/RTMP), the A/V delay ring buffer, the deadline engine, fail-safe, hold and retro-hold, compositor, x264/NVENC encoder, audio watchdog, ZeroMQ bus, WSL2/GPU setup, dev/demo profiles.

## Your demo moments
1. Architecture diagram: "Frames never leave the machine; anything unchecked goes out masked."
2. **Hold** button: slate within one frame, audio silent, back live about 4 s after release.
3. Fail-safe proof: a vision lane was killed on purpose, and 0 card frames leaked.

## Numbers
- Delay p95 4.53 s (limit 5 s); egress-socket p95 4.39 s; 30.3 fps (idle laptop, CPU path)
- 0 card leaks in 2,004 visible frames; slate fraction 9.9% (limit 10%) in the busiest run, 6.7% earlier
- Fail-safe: 0 card frames after the fault, 985 slate frames
- Hold: 0 live frames after the command (limit 1), fence 0, audio -120 dBFS, live again 4.2 s
- Encoder: x264 veryfast 720p30 about 0.75 cores, p95 encode about 16-20 ms; NVENC 0.23 cores
- SRT freeze test: 2.5 s stall -> hold within ~1.4 s; release ~0.4 s after thaw

## Questions you answer
1. **Why a 4 s delay?** Platforms already add 3-10 s; it's configurable 3-5 s and covers the check time.
2. **What is the fail-safe?** Every frame has a deadline; if the check isn't back in time the frame is masked or slated.
3. **How is A/V sync kept?** Both carry PTS through the ring buffer; mute spans are sample-accurate.
4. **Audio module dies?** A heartbeat watchdog mutes audio; audio is only emitted once a heartbeat vouches for it (1 s margin).
5. **Phone drops?** Ingest-drop hold within about a second, automatic recovery.
6. **Why ZeroMQ, separate relay?** Multi-process, hold commands on a never-dropped queue, a killable muxer worker (PyAV once held the GIL and deadlocked).
7. **GPU/CPU budget?** CPU-capable everywhere; GPU optional; 3.5 GB cap in dev profile; one session uses roughly 4 cores.
8. **What happens under overload?** The fail-safe shows the privacy slate instead of risking a leak.

## Volunteer these
- G1 failed once on a loaded machine; root cause found and fixed (motion-history size).
- Under heavy load the engine slates more; the demo needs a quiet machine.
- A relay reconnect bug is being fixed: after a sender restart the protected output may not return.
- 4G test was loopback netem only; no real phone or demo laptop yet.
- The last 1-2 s of a finite stream goes out muted (watchdog margin); live streams are unaffected.

## If pushed
- "Is it live-proven?" -> "No. Local end-to-end with synthetic streams; live platforms need keys."
- "Why not just lower the delay?" -> "Delay is the verification window; lower means more slate."
- "What's your worst case?" -> "Overload: viewers see a cover screen, never an unchecked frame."
