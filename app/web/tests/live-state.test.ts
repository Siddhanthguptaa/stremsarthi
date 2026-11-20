import { describe, expect, it } from "vitest"

import { engineFrame, safetyFrame, sampleData } from "./helpers"
import { categoryKey, formatPercent, formatTime, holdSourceKey, safetyActionKey } from "@/lib/format"
import { en, hi } from "@/lib/i18n"
import {
  type ApiAlert,
  chipFor,
  ENGINE_STALE_MS,
  initialLiveState,
  filterAlerts,
  liveReducer,
  MAX_ALERTS,
  toAlert,
} from "@/lib/live-state"
import { CATEGORIES, HOLD_SOURCES, type EngineStatus, type SafetyEvent } from "@/lib/wire"

const event = (over: Partial<SafetyEvent> = {}): SafetyEvent => ({
  ...(safetyFrame(1).data.d as unknown as SafetyEvent),
  ...over,
})
const status = (over: Partial<EngineStatus> = {}): EngineStatus => ({
  ...(engineFrame(1).data.d as unknown as EngineStatus),
  ...over,
})

describe("alert feed", () => {
  it("keeps modality, category, action, time, confidence and duration only: no box, no codes, no text", () => {
    const e = event({ event_id: "e1", category: "aadhaar", action: "masked", confidence: 0.93 })
    const alert = toAlert(e)
    expect(Object.keys(alert).sort()).toEqual(["action", "at", "category", "confidence", "durationMs", "id", "modality"])
    const serialised = JSON.stringify(alert)
    expect(serialised).not.toContain("bbox")
    expect(serialised).not.toContain(e.source)
    expect(serialised).not.toContain(e.detail_code)
  })

  it("lists the newest first and counts every event", () => {
    let s = initialLiveState
    for (let i = 1; i <= 3; i++) s = liveReducer(s, { type: "safety", event: event({ event_id: `e${i}` }) })
    expect(s.alerts.map((a) => a.id)).toEqual(["e3", "e2", "e1"])
    expect(s.alertsTotal).toBe(3)
  })

  it("caps what it keeps at 200 (500 alerts a minute must not grow without bound)", () => {
    let s = initialLiveState
    for (let i = 1; i <= 500; i++) s = liveReducer(s, { type: "safety", event: event({ event_id: `e${i}` }) })
    expect(s.alerts).toHaveLength(MAX_ALERTS)
    expect(s.alerts[0].id).toBe("e500")
    expect(s.alerts.at(-1)?.id).toBe("e301")
    expect(s.alertsTotal).toBe(500)
  })

  it("ignores a repeated event id", () => {
    const e = event({ event_id: "dup" })
    const once = liveReducer(initialLiveState, { type: "safety", event: e })
    expect(liveReducer(once, { type: "safety", event: e })).toBe(once)
  })

  it("updates a row in place when the engine republishes it with a duration (not counted twice)", () => {
    let s = initialLiveState
    for (const id of ["a", "b", "c"]) s = liveReducer(s, { type: "safety", event: event({ event_id: id, duration_ms: null }) })
    s = liveReducer(s, { type: "safety", event: event({ event_id: "b", duration_ms: 1800 }) })
    expect(s.alerts.map((a) => [a.id, a.durationMs])).toEqual([["c", null], ["b", 1800], ["a", null]])
    expect(s.alertsTotal).toBe(3)
  })

  const apiAlert = (id: string, at: string, over: Partial<ApiAlert> = {}): ApiAlert => ({
    id,
    at,
    modality: "audio",
    category: "spoken_phone",
    action: "muted",
    confidence: 0.8,
    duration_ms: null,
    ...over,
  })

  it("backfills missed alerts from REST in time order without duplicating live ones", () => {
    let s = liveReducer(initialLiveState, { type: "safety", event: event({ event_id: "live1", wall_ts: "2026-11-21T09:00:05+00:00" }) })
    s = liveReducer(s, {
      type: "backfill",
      alerts: [
        apiAlert("live1", "2026-11-21T09:00:05+00:00"),
        apiAlert("old2", "2026-11-21T09:00:03+00:00"),
        apiAlert("old1", "2026-11-21T09:00:01+00:00"),
        apiAlert("newer", "2026-11-21T09:00:09+00:00"),
      ],
    })
    expect(s.alerts.map((a) => a.id)).toEqual(["newer", "live1", "old2", "old1"])
    expect(s.alertsTotal).toBe(4)
    const again = liveReducer(s, { type: "backfill", alerts: [apiAlert("old1", "2026-11-21T09:00:01+00:00")] })
    expect(again).toBe(s)
  })

  it("a backfill never grows the list past 200", () => {
    const many = Array.from({ length: 300 }, (_, i) => apiAlert(`x${i}`, new Date(1_700_000_000_000 + i * 1000).toISOString()))
    const s = liveReducer(initialLiveState, { type: "backfill", alerts: many })
    expect(s.alerts).toHaveLength(MAX_ALERTS)
    expect(s.alerts[0].id).toBe("x299")
  })

  it("filters by modality; guard and system alerts only show under all", () => {
    const alerts = [
      toAlert(event({ event_id: "v", modality: "video" })),
      toAlert(event({ event_id: "a", modality: "audio" })),
      toAlert(event({ event_id: "g", modality: "guard" })),
    ]
    expect(filterAlerts(alerts, "all")).toHaveLength(3)
    expect(filterAlerts(alerts, "audio").map((a) => a.id)).toEqual(["a"])
    expect(filterAlerts(alerts, "chat")).toEqual([])
  })

  it("every category and action has a label in both languages", () => {
    for (const c of CATEGORIES) {
      expect(en[categoryKey(c)], c).toBeTruthy()
      expect(hi[categoryKey(c)], c).toBeTruthy()
    }
    for (const a of ["masked", "muted", "hidden", "held", "deleted", "timed_out", "warned", "failsafe"] as const) {
      expect(en[safetyActionKey(a)], a).toBeTruthy()
      expect(hi[safetyActionKey(a)], a).toBeTruthy()
    }
    for (const h of HOLD_SOURCES) expect(hi[holdSourceKey(h)], h).toBeTruthy()
  })
})

describe("status chip", () => {
  const at = 1_000_000
  const withEngine = (over: Partial<EngineStatus>) =>
    liveReducer(initialLiveState, { type: "engine", status: status(over), at })

  it("hold wins over everything", () => {
    expect(chipFor(withEngine({ state: "live" }), true, at, true)).toBe("hold")
    expect(chipFor(initialLiveState, true, at, false)).toBe("hold")
  })

  it("LIVE when every frame is checked, PROTECT when the engine is on the slate or a lane is stale", () => {
    expect(chipFor(withEngine({ state: "live", protect_seconds: 0, lanes_stale: [] }), false, at, true)).toBe("live")
    expect(chipFor(withEngine({ state: "live", protect_seconds: 1.5, lanes_stale: [] }), false, at, true)).toBe("protect")
    expect(chipFor(withEngine({ state: "live", protect_seconds: 0, lanes_stale: ["accurate"] }), false, at, true)).toBe("protect")
  })

  it("maps the other engine states", () => {
    const kinds = {
      starting: "starting",
      ready: "starting",
      waiting_ingest: "waiting",
      stopping: "stopping",
      error: "error",
    } as const
    for (const [state, kind] of Object.entries(kinds)) {
      expect(chipFor(withEngine({ state: state as EngineStatus["state"] }), false, at, true)).toBe(kind)
    }
  })

  it("an engine that stops reporting is OFFLINE; one never heard from is just unknown", () => {
    const s = withEngine({ state: "live" })
    expect(chipFor(s, false, at + ENGINE_STALE_MS, true)).toBe("live")
    expect(chipFor(s, false, at + ENGINE_STALE_MS + 1, true)).toBe("offline")
    expect(chipFor(initialLiveState, false, at, false)).toBe("unknown")
  })

  it("the api flag is tracked", () => {
    const down = liveReducer(initialLiveState, { type: "api", down: true })
    expect(down.apiDown).toBe(true)
    expect(liveReducer(down, { type: "api", down: true })).toBe(down)
  })
})

describe("format", () => {
  it("formats confidence as a clamped percentage", () => {
    expect(formatPercent(0.934)).toBe("93%")
    expect(formatPercent(1.4)).toBe("100%")
    expect(formatPercent(-1)).toBe("0%")
  })

  it("formats times with Latin digits in both languages and tolerates junk", () => {
    const en = formatTime("2026-11-20T14:03:09+00:00", "en")
    const hi = formatTime("2026-11-20T14:03:09+00:00", "hi")
    expect(en).toMatch(/^[\d:\sAMPamp.]+$/)
    expect(hi).toMatch(/\d{1,2}:\d{2}:\d{2}/) // Latin digits, not Devanagari
    expect(formatTime("not a date", "en")).toBe("")
  })

  it("the sample engine status is the shape the chip reads", () => {
    expect(sampleData<EngineStatus>("EngineStatus")).toHaveProperty("protect_seconds")
  })
})
