import { describe, expect, it } from "vitest"

import { initialRisk, levelForScore, METER_STALE_MS, riskReducer, riskView } from "@/lib/risk"
import type { RiskMeter, RiskSignal } from "@/lib/wire"

const signal = (over: Partial<RiskSignal> = {}): RiskSignal => ({
  wall_ts: "2026-11-21T10:00:00+00:00",
  pts: null,
  source: "fingerprint",
  category: "copyright_match",
  score: 0.9,
  ttl_s: 10,
  label: "fp:demo_03",
  ...over,
})
const meter = (over: Partial<RiskMeter> = {}): RiskMeter => ({
  wall_ts: "2026-11-21T10:00:00+00:00",
  level: "red",
  score: 0.95,
  top_sources: ["fingerprint"],
  ...over,
})

describe("riskReducer", () => {
  it("takes the meter level and keeps signals until their ttl ends", () => {
    let s = riskReducer(initialRisk, { type: "meter", meter: meter(), at: 1000 })
    s = riskReducer(s, { type: "signal", signal: signal(), at: 1000 })
    expect(riskView(s, 2000)).toMatchObject({ level: "red", score: 0.95 })
    expect(riskView(s, 2000).signals).toHaveLength(1)
    expect(riskView(s, 11_001).signals).toHaveLength(0)
  })
  it("never keeps the free-form label of a signal", () => {
    const s = riskReducer(initialRisk, { type: "signal", signal: signal(), at: 0 })
    expect(JSON.stringify(s)).not.toContain("fp:demo_03")
    expect(Object.keys(s.signals[0]).sort()).toEqual(["category", "expiresAt", "key", "score", "source"])
  })
  it("replaces a signal of the same source and category instead of stacking", () => {
    let s = riskReducer(initialRisk, { type: "signal", signal: signal({ score: 0.4 }), at: 0 })
    s = riskReducer(s, { type: "signal", signal: signal({ score: 0.8 }), at: 5 })
    expect(s.signals).toHaveLength(1)
    expect(s.signals[0].score).toBe(0.8)
  })
  it("falls back to the strongest signal when the meter reading is stale", () => {
    let s = riskReducer(initialRisk, { type: "meter", meter: meter({ level: "red", score: 0.95 }), at: 0 })
    s = riskReducer(s, { type: "signal", signal: signal({ score: 0.4, ttl_s: 120 }), at: METER_STALE_MS })
    expect(riskView(s, METER_STALE_MS + 1)).toMatchObject({ level: "yellow", score: 0.4 })
  })
  it("is green with nothing", () => {
    expect(riskView(initialRisk, 0)).toEqual({ level: "green", score: 0, signals: [] })
  })
  it("maps the REST snapshot levels and clamps scores", () => {
    const s = riskReducer(initialRisk, { type: "snapshot", snapshot: { level: "warn", score: 7, signals: [signal()] }, at: 0 })
    expect(riskView(s, 1)).toMatchObject({ level: "orange", score: 1 })
    expect(s.signals).toHaveLength(1)
  })
  it("drops expired signals on tick and returns the same state when nothing expired", () => {
    const s = riskReducer(initialRisk, { type: "signal", signal: signal({ ttl_s: 1 }), at: 0 })
    expect(riskReducer(s, { type: "tick", now: 500 })).toBe(s)
    expect(riskReducer(s, { type: "tick", now: 1500 }).signals).toHaveLength(0)
  })
  it("levels by score", () => {
    expect([0, 0.3, 0.6, 0.85].map(levelForScore)).toEqual(["green", "yellow", "orange", "red"])
  })
})
