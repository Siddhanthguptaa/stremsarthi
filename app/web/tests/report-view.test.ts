import { describe, expect, it } from "vitest"

import { parseReport } from "@/lib/report-types"
import { buildTimeline, categoryRows, formatDuration, holdSeconds, offsetLabel, peakEvents, reportSpan, totalHoldSeconds } from "@/lib/report-view"
import { reportFixture } from "./report-fixtures"

describe("report view helpers", () => {
  const report = parseReport(reportFixture())

  it("builds one lane per category, busiest first, with positions inside 0..1", () => {
    const m = buildTimeline(report)
    expect(m.lanes.map((l) => [l.category, l.count])).toEqual([
      ["face", 2],
      ["spoken_phone", 1],
      ["upi_qr", 1],
    ])
    for (const lane of m.lanes) {
      for (const mark of lane.marks) {
        expect(mark.at).toBeGreaterThanOrEqual(0)
        expect(mark.at).toBeLessThanOrEqual(1)
      }
    }
    expect(m.holds).toHaveLength(1)
    expect(m.holds[0].from).toBeCloseTo(100 / 600)
    expect(m.holds[0].to).toBeCloseTo(130 / 600)
    expect(m.capped).toBe(false)
  })

  it("never has a zero-width axis", () => {
    const one = parseReport(reportFixture({ started_at: null, ended_at: null, timeline: [reportFixture().timeline[0]], holds: [] }))
    const span = reportSpan(one)
    expect(span.endMs - span.startMs).toBeGreaterThanOrEqual(1000)
  })

  it("runs an open hold to the end of the session", () => {
    const open = parseReport(reportFixture({ holds: [{ start_ts: "2026-11-20T10:09:00Z", end_ts: null, sources: ["hotkey"] }] }))
    const span = reportSpan(open)
    expect(totalHoldSeconds(open, span)).toBeCloseTo(60)
    expect(holdSeconds("2026-11-20T10:09:00Z", null, span)).toBeCloseTo(60)
  })

  it("formats durations and offsets", () => {
    expect(formatDuration(45)).toBe("45 s")
    expect(formatDuration(125)).toBe("2 min 5 s")
    expect(formatDuration(3725)).toBe("1 h 2 min 5 s")
    expect(formatDuration(null)).toBe("-")
    expect(formatDuration(-1)).toBe("-")
    expect(offsetLabel("2026-11-20T10:01:30Z", reportSpan(report))).toBe("+01:30")
  })

  it("lists categories by count and the most confident events", () => {
    expect(categoryRows(report)[0]).toEqual({ category: "face", count: 2, mean: 0.9 })
    expect(peakEvents(report, 2).map((e) => e.event_id)).toEqual(["e2", "e1"])
  })
})
