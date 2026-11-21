import { describe, expect, it, vi } from "vitest"

import { type DemoStatus, createDemoApi, parseDemoStatus, parsePreviewToken, previewUrl } from "@/lib/demo-api"
import { demoEn, demoHi, demoText } from "@/lib/demo-i18n"
import { initialProof, proofReducer, proofRows, MAX_REMEMBERED_IDS } from "@/lib/demo-proof"
import {
  RETRY_MAX_MS,
  TOKEN_REFRESH_MIN_MS,
  TOKEN_REFRESH_MS,
  paneMode,
  retryDelayMs,
  stageState,
  streamAvailable,
  tokenRefreshDelayMs,
  viewerCapReached,
} from "@/lib/demo-stream"
import type { ApiAlert } from "@/lib/live-state"

const status = (over: Partial<DemoStatus> = {}): DemoStatus => ({
  raw_enabled: true,
  protected_available: true,
  raw_available: true,
  clients: 0,
  clients_by_stream: {},
  max_clients: null,
  ...over,
})

describe("retry and refresh timing", () => {
  it("backs off 1, 2, 4, 8 s then caps at 15 s (no jitter)", () => {
    const mid = () => 0.5 // factor 1.0
    expect([0, 1, 2, 3, 4, 5, 20].map((n) => retryDelayMs(n, mid))).toEqual([1000, 2000, 4000, 8000, 15000, 15000, 15000])
  })

  it("keeps the jitter within +-20 %", () => {
    expect(retryDelayMs(2, () => 0)).toBe(3200)
    expect(retryDelayMs(2, () => 1)).toBe(4800)
    expect(retryDelayMs(30, () => 1)).toBeLessThanOrEqual(RETRY_MAX_MS * 1.2)
  })

  it("refreshes the token every 4 minutes, sooner only for a shorter-lived token", () => {
    expect(TOKEN_REFRESH_MS).toBe(240_000)
    expect(tokenRefreshDelayMs(900)).toBe(TOKEN_REFRESH_MS)
    expect(tokenRefreshDelayMs(120)).toBe(100_000)
    expect(tokenRefreshDelayMs(5)).toBe(TOKEN_REFRESH_MIN_MS)
    expect(tokenRefreshDelayMs(Number.NaN)).toBe(TOKEN_REFRESH_MS)
  })
})

describe("paneMode", () => {
  it("raw preview off: the raw pane is a placeholder but the protected pane still streams", () => {
    const s = status({ raw_enabled: false, raw_available: false })
    expect(paneMode("raw", s, false)).toBe("disabled")
    expect(paneMode("protected", s, false)).toBe("stream")
  })

  it("waits for a stream that has no frames yet", () => {
    expect(paneMode("raw", status({ raw_available: false }), false)).toBe("waiting")
    expect(paneMode("protected", status({ protected_available: false }), false)).toBe("waiting")
    expect(paneMode("raw", status(), false)).toBe("stream")
  })

  it("before the first status reply it connects; if the call fails only the protected stream is tried", () => {
    expect(paneMode("raw", null, false)).toBe("connecting")
    expect(paneMode("protected", null, false)).toBe("connecting")
    expect(paneMode("raw", null, true)).toBe("unavailable")
    expect(paneMode("protected", null, true)).toBe("stream")
  })
})

describe("stageState", () => {
  it("maps the engine chip to LIVE / HOLD / SLATE", () => {
    expect(stageState("live")).toBe("live")
    expect(stageState("hold")).toBe("hold")
    expect(stageState("protect")).toBe("slate")
    expect(stageState("offline")).toBe("other")
  })
})

describe("demo API client", () => {
  it("builds the stream URL with the token as the only, URL-encoded parameter", () => {
    expect(previewUrl("raw", "a b+c/=", "http://api.test/")).toBe("http://api.test/api/demo/preview/raw.mjpg?t=a%20b%2Bc%2F%3D")
    expect(previewUrl("protected", "T", "http://api.test")).toBe("http://api.test/api/demo/preview/protected.mjpg?t=T")
  })

  it("posts for a token with the bearer header and parses the reply", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ token: "tok123", expires_in_s: 300 }), { status: 200 }))
    const api = createDemoApi({ baseUrl: "http://api.test", getToken: () => "STREAMER", fetchImpl: fetchImpl as unknown as typeof fetch })
    await expect(api.getPreviewToken()).resolves.toEqual({ token: "tok123", expires_in_s: 300 })
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("http://api.test/api/demo/preview-token")
    expect(init.method).toBe("POST")
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer STREAMER")
    // the preview token never travels in a query string of the API call or in a header
    expect(url).not.toContain("tok123")
  })

  it("reads the status, tolerating missing fields", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ raw_enabled: false, clients: 2 }), { status: 200 }))
    const api = createDemoApi({ baseUrl: "http://api.test", getToken: () => "S", fetchImpl: fetchImpl as unknown as typeof fetch })
    await expect(api.getStatus()).resolves.toEqual({ raw_enabled: false, protected_available: false, raw_available: false, clients: 2, clients_by_stream: {}, max_clients: null })
  })

  it("turns a 401 into the sign-out callback and a network failure into a status-0 error", async () => {
    const onUnauthorized = vi.fn()
    const unauthorised = createDemoApi({
      baseUrl: "http://api.test",
      getToken: () => "S",
      onUnauthorized,
      fetchImpl: (async () => new Response("{}", { status: 401 })) as unknown as typeof fetch,
    })
    await expect(unauthorised.getStatus()).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledOnce()

    const down = createDemoApi({
      baseUrl: "http://api.test",
      getToken: () => "S",
      fetchImpl: (async () => {
        throw new TypeError("offline")
      }) as unknown as typeof fetch,
    })
    await expect(down.getPreviewToken()).rejects.toMatchObject({ status: 0, isNetwork: true })
  })

  it("reads max_clients and per-stream client counts, and decides when a stream is full", () => {
    const parsed = parseDemoStatus({ raw_enabled: true, protected_available: true, raw_available: true, clients: { raw: 1, protected: 2 }, max_clients: 2 })
    expect(parsed).toMatchObject({ clients: 3, clients_by_stream: { raw: 1, protected: 2 }, max_clients: 2 })
    expect(viewerCapReached("protected", parsed as DemoStatus)).toBe(true)
    expect(viewerCapReached("raw", parsed as DemoStatus)).toBe(false)
    // a plain number is the total; no cap means never full
    expect(viewerCapReached("raw", status({ clients: 5, max_clients: 5 }))).toBe(true)
    expect(viewerCapReached("raw", status({ clients: 50, max_clients: null }))).toBe(false)
    expect(parseDemoStatus({ max_clients: 0 })?.max_clients).toBeNull()
  })

  it("knows when a stream is available", () => {
    expect(streamAvailable("protected", status({ protected_available: true }))).toBe(true)
    expect(streamAvailable("raw", status({ raw_enabled: false, raw_available: true }))).toBe(false)
  })

  it("rejects malformed bodies", () => {
    expect(parsePreviewToken({ token: "" })).toBeNull()
    expect(parsePreviewToken(null)).toBeNull()
    expect(parseDemoStatus("x")).toBeNull()
  })
})

describe("demo strings", () => {
  it("has every key in English and Hindi, none empty", () => {
    expect(Object.keys(demoHi).sort()).toEqual(Object.keys(demoEn).sort())
    for (const key of Object.keys(demoEn) as (keyof typeof demoEn)[]) {
      expect(demoEn[key].length).toBeGreaterThan(0)
      expect(demoHi[key].length).toBeGreaterThan(0)
    }
    expect(demoText("hi", "stage.protected")).toBe("दर्शकों को क्या मिलता है")
  })

  it("explains how to turn raw preview on, in both languages", () => {
    expect(demoEn["raw.disabled.body"]).toContain("DEMO_RAW_PREVIEW=1")
    expect(demoHi["raw.disabled.body"]).toContain("DEMO_RAW_PREVIEW=1")
    expect(demoEn["raw.disabled.body"].toLowerCase()).toContain("loopback")
  })
})

describe("privacy proof counter", () => {
  const ev = (id: string, category: "aadhaar" | "face" | "spoken_phone" | "system_status", modality: "video" | "audio" | "system" = "video") => ({
    type: "event" as const,
    event: { event_id: id, category, modality, wall_ts: "2026-11-21T09:00:00+00:00" },
  })

  it("counts by category and modality, newest events included", () => {
    let s = initialProof
    for (const e of [ev("1", "aadhaar"), ev("2", "aadhaar"), ev("3", "spoken_phone", "audio")]) s = proofReducer(s, e)
    expect(s.total).toBe(3)
    expect(s.byCategory).toEqual({ aadhaar: 2, spoken_phone: 1 })
    expect(s.byModality).toEqual({ video: 2, audio: 1 })
    expect(proofRows(s)).toEqual([
      { category: "aadhaar", count: 2 },
      { category: "spoken_phone", count: 1 },
    ])
  })

  it("never counts an event twice (republish with a duration, replay)", () => {
    let s = proofReducer(initialProof, ev("1", "aadhaar"))
    const again = proofReducer(s, ev("1", "aadhaar"))
    expect(again).toBe(s)
    s = again
    expect(s.total).toBe(1)
  })

  it("ignores system status events", () => {
    const s = proofReducer(initialProof, ev("9", "system_status", "system"))
    expect(s.total).toBe(0)
  })

  it("backfills only alerts from the current session and does not double count live ones", () => {
    const alert = (id: string, at: string): ApiAlert => ({
      id,
      at,
      modality: "video",
      category: "face",
      action: "masked",
      confidence: 0.9,
      duration_ms: null,
    })
    let s = proofReducer(initialProof, ev("live1", "face"))
    s = proofReducer(s, {
      type: "backfill",
      since: Date.parse("2026-11-21T09:00:00+00:00"),
      alerts: [alert("old", "2026-11-21T08:00:00+00:00"), alert("live1", "2026-11-21T09:05:00+00:00"), alert("new", "2026-11-21T09:06:00+00:00")],
    })
    expect(s.total).toBe(2)
    expect(s.byCategory).toEqual({ face: 2 })
  })

  it("resets, and bounds the memory of seen ids", () => {
    let s = proofReducer(initialProof, ev("1", "face"))
    expect(proofReducer(s, { type: "reset" }).total).toBe(0)
    for (let i = 0; i < MAX_REMEMBERED_IDS + 5; i++) s = proofReducer(s, ev(`e${i}`, "face"))
    expect(s.seen.size).toBeLessThanOrEqual(MAX_REMEMBERED_IDS)
    expect(s.total).toBe(MAX_REMEMBERED_IDS + 6)
  })

  it("keeps nothing but ids, categories and counts", () => {
    const s = proofReducer(initialProof, ev("1", "aadhaar"))
    expect(Object.keys(s).sort()).toEqual(["byCategory", "byModality", "seen", "total"])
  })
})
