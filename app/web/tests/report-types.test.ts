import { describe, expect, it } from "vitest"

import { parseReport, ReportShapeError, safeCode, safeNote, toIso } from "@/lib/report-types"
import { reportFixture } from "./report-fixtures"

const PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="

describe("parseReport", () => {
  it("accepts the documented shape and keeps its numbers", () => {
    const r = parseReport(reportFixture())
    expect(r.session_id).toBe("sess-abc_123")
    expect(r.timeline).toHaveLength(4)
    expect(r.totals.by_category.face).toEqual({ count: 2, mean_confidence: 0.9 })
    expect(r.holds).toEqual([{ start_ts: "2026-11-20T10:01:40.000Z", end_ts: "2026-11-20T10:02:10.000Z", sources: ["app"] }])
    expect(r.risk.max_level).toBe("orange")
    expect(r.chat.platform_actions).toEqual({ done: 3, failed: 1, unsupported: 2 })
  })

  it("rejects things that are not a v1 report", () => {
    expect(() => parseReport(null)).toThrow(ReportShapeError)
    expect(() => parseReport([])).toThrow(ReportShapeError)
    expect(() => parseReport({ ...reportFixture(), schema_version: 2 })).toThrow(ReportShapeError)
    expect(() => parseReport({ ...reportFixture(), timeline: "x" })).toThrow(ReportShapeError)
  })

  it("drops fields the schema does not list, so they can never be shown or saved", () => {
    const dirty = reportFixture()
    ;(dirty.timeline[0] as Record<string, unknown>).message_text = "some viewer text"
    ;(dirty.timeline[0] as Record<string, unknown>).image = PNG_B64
    ;(dirty as Record<string, unknown>).screenshot = `data:image/png;base64,${PNG_B64}`
    const json = JSON.stringify(parseReport(dirty))
    expect(json).not.toContain("some viewer text")
    expect(json).not.toContain(PNG_B64)
    expect(json).not.toContain("screenshot")
    expect(json).not.toContain("message_text")
  })

  it("blanks base64 blobs and long strings in code fields, and drops unsafe notes", () => {
    const dirty = reportFixture()
    ;(dirty.timeline[0] as Record<string, unknown>).detail_code = PNG_B64
    ;(dirty.timeline[1] as Record<string, unknown>).source = "x".repeat(200)
    dirty.notes = ["ok note", `blob ${PNG_B64}`, "<script>alert(1)</script>", "y".repeat(500)]
    const r = parseReport(dirty)
    expect(r.timeline[0].detail_code).toBe("")
    expect(r.timeline[1].source).toBe("")
    expect(r.notes).toEqual(["ok note"])
  })

  it("drops an event with no usable time or category, clamps confidence, and sorts by time", () => {
    const dirty = reportFixture()
    dirty.timeline = [
      { ...dirty.timeline[1], confidence: 7 },
      { ...dirty.timeline[0], wall_ts: "not a time" },
      { ...dirty.timeline[2], category: "has spaces <b>" },
      dirty.timeline[3],
      dirty.timeline[0],
    ]
    const r = parseReport(dirty)
    expect(r.timeline.map((e) => e.event_id)).toEqual(["e1", "e2", "e4"])
    expect(r.timeline.find((e) => e.event_id === "e2")?.confidence).toBe(1)
  })

  it("derives totals from the timeline when the server sent none, and accepts epoch seconds", () => {
    const dirty = reportFixture({ totals: undefined, started_at: 1_763_632_800, ended_at: null })
    const r = parseReport(dirty)
    expect(r.totals.by_category.face.count).toBe(2)
    expect(r.totals.by_action).toEqual({ masked: 3, muted: 1 })
    expect(r.started_at).toBe("2025-11-20T10:00:00.000Z")
    expect(r.ended_at).toBeNull()
  })

  it("keeps a bounding box only when it is four numeric points", () => {
    const dirty = reportFixture()
    ;(dirty.timeline[0] as Record<string, unknown>).bbox = { pts: [[1, 2], "x", [3, 4], [5, 6]] }
    const r = parseReport(dirty)
    expect(r.timeline.find((e) => e.event_id === "e1")?.bbox).toBeNull()
    expect(r.timeline.find((e) => e.event_id === "e2")?.bbox).toHaveLength(4)
  })

  it("handles a report with zero events", () => {
    const r = parseReport(reportFixture({ timeline: [], totals: { by_category: {}, by_action: {}, by_modality: {} }, holds: [] }))
    expect(r.timeline).toEqual([])
    expect(r.totals.by_category).toEqual({})
  })
})

describe("small sanitisers", () => {
  it("safeCode keeps short codes only", () => {
    expect(safeCode("fp:demo_03")).toBe("fp:demo_03")
    expect(safeCode("a".repeat(65))).toBe("")
    expect(safeCode("<b>")).toBe("")
    expect(safeCode(5)).toBe("")
  })
  it("safeNote strips control characters", () => {
    expect(safeNote("a\u0000b\nc")).toBe("a b c")
  })
  it("toIso normalises and refuses garbage", () => {
    expect(toIso("2026-11-20T10:00:00+05:30")).toBe("2026-11-20T04:30:00.000Z")
    expect(toIso("nope")).toBeNull()
    expect(toIso(null)).toBeNull()
    expect(toIso(Number.NaN)).toBeNull()
  })
})
