import { describe, expect, it } from "vitest"

import { ApiRequestError } from "@/lib/api"
import {
  CAPTURE_DURATION_MS,
  CAPTURE_FRAMES,
  cameraFailure,
  captureOffsets,
  cleanLabel,
  enrolErrorKey,
  expiryInfo,
  frameStats,
  guidanceFor,
} from "@/lib/enrol"
import { en } from "@/lib/i18n"

function rgba(w: number, h: number, pixel: (x: number, y: number) => number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4)
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const v = pixel(x, y)
      out.set([v, v, v, 255], (y * w + x) * 4)
    }
  return out
}

describe("capture plan", () => {
  it("takes 8 frames over about 3 seconds, the first at once and the last at the end", () => {
    const offsets = captureOffsets()
    expect(offsets).toHaveLength(CAPTURE_FRAMES)
    expect(CAPTURE_FRAMES).toBe(8)
    expect(offsets[0]).toBe(0)
    expect(offsets.at(-1)).toBe(CAPTURE_DURATION_MS)
    expect([...offsets].sort((a, b) => a - b)).toEqual(offsets)
  })

  it("handles one frame and none", () => {
    expect(captureOffsets(1)).toEqual([0])
    expect(captureOffsets(0)).toEqual([])
  })
})

describe("live guidance", () => {
  const checker = rgba(32, 24, (x, y) => ((x + y) % 2 ? 230 : 120)) // textured, mid brightness
  const flatGrey = rgba(32, 24, () => 128)

  it("measures brightness and sharpness", () => {
    expect(frameStats(rgba(8, 8, () => 0), 8, 8).luma).toBe(0)
    expect(frameStats(rgba(8, 8, () => 255), 8, 8).luma).toBeCloseTo(255, 0)
    expect(frameStats(flatGrey, 32, 24).sharpness).toBe(0)
    expect(frameStats(checker, 32, 24).sharpness).toBeGreaterThan(50)
    expect(frameStats(new Uint8ClampedArray(0), 0, 0)).toEqual({ luma: 0, sharpness: 0 })
  })

  it("tells the streamer the most important fix first", () => {
    const good = { luma: 120, sharpness: 20 }
    expect(guidanceFor(good, null)).toBe("ok")
    expect(guidanceFor({ ...good, luma: 20 }, null)).toBe("tooDark")
    expect(guidanceFor({ ...good, luma: 245 }, null)).toBe("tooBright")
    expect(guidanceFor({ ...good, sharpness: 0.5 }, null)).toBe("blurry")
    expect(guidanceFor(good, { count: 0, largestWidthRatio: 0 })).toBe("noFace")
    expect(guidanceFor(good, { count: 2, largestWidthRatio: 0.4 })).toBe("multiple")
    expect(guidanceFor(good, { count: 1, largestWidthRatio: 0.1 })).toBe("tooSmall")
    expect(guidanceFor(good, { count: 1, largestWidthRatio: 0.4 })).toBe("ok")
    expect(guidanceFor({ ...good, luma: 20 }, { count: 0, largestWidthRatio: 0 })).toBe("tooDark")
  })
})

describe("expiry", () => {
  const now = Date.parse("2026-11-20T10:00:00Z")
  it("counts down in hours and minutes", () => {
    expect(expiryInfo("2026-11-20T21:40:00Z", now)).toEqual({ kind: "in", hours: 11, minutes: 40 })
    expect(expiryInfo("2026-11-20T10:00:30Z", now)).toEqual({ kind: "in", hours: 0, minutes: 1 })
  })
  it("knows about expired and never", () => {
    expect(expiryInfo("2026-11-20T09:59:00Z", now)).toEqual({ kind: "expired" })
    expect(expiryInfo(null, now)).toEqual({ kind: "never" })
    expect(expiryInfo("not a date", now)).toEqual({ kind: "never" })
  })
})

describe("camera errors, labels and error messages", () => {
  it("separates a blocked camera from a missing one", () => {
    expect(cameraFailure({ name: "NotAllowedError" })).toBe("denied")
    expect(cameraFailure({ name: "SecurityError" })).toBe("denied")
    expect(cameraFailure({ name: "NotFoundError" })).toBe("unavailable")
    expect(cameraFailure(new TypeError("no mediaDevices"))).toBe("unavailable")
    expect(cameraFailure(null)).toBe("unavailable")
  })

  it("cleans the label the way the API does", () => {
    expect(cleanLabel("  Asha   Rao  ")).toBe("Asha Rao")
    expect(cleanLabel("x".repeat(60))).toHaveLength(40)
    expect(cleanLabel("   ")).toBe("")
  })

  it("maps API error codes to translated messages and never shows the server's text", () => {
    const err = (status: number, code: string) => new ApiRequestError(status, code, "SERVER DETAIL TEXT")
    expect(enrolErrorKey(err(422, "too_few_faces"))).toBe("enrol.err.too_few_faces")
    expect(enrolErrorKey(err(422, "allowlist_full"))).toBe("enrol.err.allowlist_full")
    expect(enrolErrorKey(err(503, "enroller_unavailable"))).toBe("enrol.err.enroller_unavailable")
    expect(enrolErrorKey(err(429, "rate_limited"))).toBe("enrol.err.rate_limited")
    expect(enrolErrorKey(err(429, "busy"))).toBe("enrol.err.busy")
    expect(enrolErrorKey(err(500, "enrol_failed"))).toBe("enrol.err.generic")
    expect(enrolErrorKey(new ApiRequestError(0, "network"))).toBe("enrol.err.network")
    expect(enrolErrorKey(new Error("boom"))).toBe("enrol.err.generic")
    for (const code of ["too_few_faces", "allowlist_full", "enroller_unavailable", "rate_limited", "busy"]) {
      expect(en[enrolErrorKey(err(422, code))]).toBeTruthy()
    }
  })
})
