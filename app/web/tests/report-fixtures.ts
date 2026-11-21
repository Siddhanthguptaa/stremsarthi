/** A synthetic safety report in the API's schema 5.9 shape (SPECIMEN data: no real person, no text, no images). */
export function reportFixture(over: Record<string, unknown> = {}) {
  const t0 = Date.parse("2026-11-20T10:00:00Z")
  const at = (s: number) => new Date(t0 + s * 1000).toISOString()
  const ev = (n: number, s: number, category: string, action: string, confidence: number, modality = "video") => ({
    event_id: `e${n}`,
    wall_ts: at(s),
    pts: s,
    modality,
    category,
    action,
    confidence,
    bbox: modality === "video" ? { pts: [[10, 20], [110, 20], [110, 80], [10, 80]] } : null,
    duration_ms: 1200,
    source: category,
    detail_code: "specimen",
  })
  return {
    schema_version: 1,
    session_id: "sess-abc_123",
    started_at: at(0),
    ended_at: at(600),
    duration_s: 600,
    profile: "dev",
    delay_seconds: 4,
    totals: {
      by_category: {
        face: { count: 2, mean_confidence: 0.9 },
        upi_qr: { count: 1, mean_confidence: 0.97 },
        spoken_phone: { count: 1, mean_confidence: 0.8 },
      },
      by_action: { masked: 3, muted: 1 },
      by_modality: { video: 3, audio: 1 },
    },
    timeline: [
      ev(1, 30, "face", "masked", 0.92),
      ev(2, 90, "upi_qr", "masked", 0.97),
      ev(3, 200, "spoken_phone", "muted", 0.8, "audio"),
      ev(4, 400, "face", "masked", 0.88),
    ],
    holds: [{ start_ts: at(100), end_ts: at(130), sources: ["app"] }],
    failsafe: { protect_seconds: 2.5, audio_failsafe_seconds: 0 },
    latency: { delay_p50_s: 3.9, delay_p95_s: 4.4, fps_mean: 29.6 },
    risk: { max_level: "orange", warnings: [{ ts: at(250), source: "fingerprint", label: "fp:demo_03", score: 0.9 }] },
    chat: {
      by_action: { hide: 4, hold_for_mod: 2 },
      by_category: { chat_abuse: 5, chat_scam_link: 1 },
      platform_actions: { done: 3, failed: 1, unsupported: 2 },
    },
    notes: ["Specimen stream for tests."],
    ...over,
  }
}
