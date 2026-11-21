import { describe, expect, it } from "vitest"

import { ApiRequestError, type PublicConfig } from "@/lib/api"
import {
  buildStartRequest,
  clampDelay,
  formFromConfig,
  isActiveState,
  keyConfigured,
  readiness,
  sessionErrorKey,
} from "@/lib/golive"

const config = (over: Record<string, unknown> = {}): PublicConfig =>
  ({
    profile: "dev",
    delay_seconds: 4,
    categories: {},
    secrets: { youtube_stream_key: "***", twitch_stream_key: null },
    destinations: [
      { kind: "youtube", enabled: true },
      { kind: "twitch", enabled: false },
    ],
    ...over,
  }) as PublicConfig

describe("key status", () => {
  it("only reads the masked marker, never a value", () => {
    expect(keyConfigured(config(), "youtube")).toBe(true)
    expect(keyConfigured(config(), "twitch")).toBe(false)
    expect(keyConfigured(null, "youtube")).toBe(false)
  })
})

describe("form and readiness", () => {
  it("starts from the saved destinations, delay and profile", () => {
    expect(formFromConfig(config({ delay_seconds: 3.5, profile: "demo" }))).toEqual({
      destinations: { youtube: true, twitch: false },
      delay: 3.5,
      profile: "demo",
    })
  })
  it("falls back to YouTube on, 4 s, dev when there is no config", () => {
    expect(formFromConfig(null)).toEqual({ destinations: { youtube: true, twitch: false }, delay: 4, profile: "dev" })
  })
  it("clamps the delay to 3.0-5.0 in 0.5 steps", () => {
    expect(clampDelay(1)).toBe(3)
    expect(clampDelay(9)).toBe(5)
    expect(clampDelay(4.3)).toBe(4.5)
    expect(clampDelay(Number.NaN)).toBe(4)
  })
  it("needs one destination with a key, and names the missing ones", () => {
    const form = formFromConfig(config())
    expect(readiness(form, config())).toEqual({ ok: true })
    expect(readiness({ ...form, destinations: { youtube: false, twitch: false } }, config())).toMatchObject({ reason: "no_destination" })
    expect(readiness({ ...form, destinations: { youtube: true, twitch: true } }, config())).toEqual({
      ok: false,
      reason: "missing_keys",
      missing: ["twitch"],
    })
  })
  it("builds the start body the API expects", () => {
    const body = buildStartRequest({ destinations: { youtube: true, twitch: false }, delay: 4.5, profile: "demo" })
    expect(body).toEqual({
      destinations: [
        { kind: "youtube", enabled: true },
        { kind: "twitch", enabled: false },
      ],
      delay_seconds: 4.5,
      profile: "demo",
    })
  })
})

describe("session state and errors", () => {
  it("treats starting, waiting, live and stopping as active", () => {
    for (const s of ["starting", "waiting_ingest", "live", "stopping"] as const) expect(isActiveState(s)).toBe(true)
    for (const s of ["idle", "ended", "failed", "engine_lost", null] as const) expect(isActiveState(s)).toBe(false)
  })
  it("maps API error codes to message keys", () => {
    expect(sessionErrorKey(new ApiRequestError(409, "session_active"))).toBe("golive.err.session_active")
    expect(sessionErrorKey(new ApiRequestError(422, "missing_keys", "missing in .env: x"))).toBe("golive.err.missing_keys")
    expect(sessionErrorKey(new ApiRequestError(422, "no_destination"))).toBe("golive.err.no_destination")
    expect(sessionErrorKey(new ApiRequestError(503, "bus_unavailable"))).toBe("golive.err.bus")
    expect(sessionErrorKey(new ApiRequestError(0, "network"))).toBe("golive.err.network")
    expect(sessionErrorKey(new ApiRequestError(401, "unauthorized"))).toBe("golive.err.unauthorized")
    expect(sessionErrorKey(new ApiRequestError(500, "boom"))).toBe("golive.err.generic")
    expect(sessionErrorKey(new Error("x"))).toBe("golive.err.generic")
  })
})
