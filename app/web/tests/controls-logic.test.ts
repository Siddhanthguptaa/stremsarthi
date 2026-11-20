import { describe, expect, it } from "vitest"

import { ApiRequestError, type CategorySettings, type PublicConfig } from "@/lib/api"
import {
  buildPatch,
  categoriesInGroup,
  CONFIRM_DISABLE,
  controlsReducer,
  failureReason,
  groupOf,
  initialControlsState,
  isSaving,
  needsDisableConfirm,
} from "@/lib/controls"

const cat = (over: Partial<CategorySettings> = {}): CategorySettings => ({
  enabled: true,
  style: "blur",
  sensitivity: 0.5,
  mask_when_unsure: true,
  padding_px: 12,
  ...over,
})
const config = (): PublicConfig => ({
  categories: { face: cat(), aadhaar: cat({ style: "solid" }), spoken_phone: cat(), chat_abuse: cat() },
})

describe("which categories need a confirmation to turn off", () => {
  it("asks for Aadhaar, PAN and UPI QR only, and only when disabling", () => {
    expect([...CONFIRM_DISABLE].sort()).toEqual(["aadhaar", "pan", "upi_qr"])
    expect(needsDisableConfirm("aadhaar", { enabled: false })).toBe(true)
    expect(needsDisableConfirm("pan", { enabled: false })).toBe(true)
    expect(needsDisableConfirm("upi_qr", { enabled: false })).toBe(true)
    expect(needsDisableConfirm("upi_qr", { enabled: true })).toBe(false)
    expect(needsDisableConfirm("upi_qr", { style: "solid" })).toBe(false)
    expect(needsDisableConfirm("face", { enabled: false })).toBe(false)
  })
})

describe("grouping", () => {
  it("puts video and spoken categories in tabs and leaves other ones out", () => {
    expect(groupOf("face")).toBe("video")
    expect(groupOf("spoken_upi")).toBe("audio")
    expect(groupOf("chat_abuse")).toBeNull()
    const cats = config().categories
    expect(categoriesInGroup(cats, "video")).toEqual(["face", "aadhaar"])
    expect(categoriesInGroup(cats, "audio")).toEqual(["spoken_phone"])
  })
})

describe("patch body", () => {
  it("is a JSON merge for one category", () => {
    expect(buildPatch("face", { sensitivity: 0.8 })).toEqual({ categories: { face: { sensitivity: 0.8 } } })
    expect(buildPatch("pan", { enabled: false, mask_when_unsure: false })).toEqual({
      categories: { pan: { enabled: false, mask_when_unsure: false } },
    })
  })
})

describe("optimistic edits", () => {
  const loaded = controlsReducer(initialControlsState, { type: "loaded", config: config() })

  it("loads categories", () => {
    expect(loaded.status).toBe("ready")
    expect(Object.keys(loaded.categories)).toContain("face")
  })

  it("applies an edit at once and marks the card as saving", () => {
    const s = controlsReducer(loaded, { type: "edit", id: 1, category: "face", change: { enabled: false } })
    expect(s.categories.face.enabled).toBe(false)
    expect(isSaving(s, "face")).toBe(true)
    expect(isSaving(s, "aadhaar")).toBe(false)
    expect(s.categories.aadhaar).toEqual(loaded.categories.aadhaar)
  })

  it("takes the API's echo on success", () => {
    let s = controlsReducer(loaded, { type: "edit", id: 1, category: "face", change: { sensitivity: 0.8 } })
    s = controlsReducer(s, {
      type: "saved",
      id: 1,
      config: { categories: { face: cat({ sensitivity: 0.8, padding_px: 20 }) } },
    })
    expect(s.categories.face.padding_px).toBe(20)
    expect(isSaving(s, "face")).toBe(false)
    expect(s.notice).toEqual({ kind: "saved", category: "face" })
  })

  it("rolls back to the earlier value when the request fails", () => {
    let s = controlsReducer(loaded, { type: "edit", id: 1, category: "face", change: { enabled: false, style: "solid" } })
    s = controlsReducer(s, { type: "failed", id: 1, reason: "failed" })
    expect(s.categories.face).toEqual(loaded.categories.face)
    expect(s.notice).toEqual({ kind: "reverted", category: "face", reason: "failed" })
    expect(isSaving(s, "face")).toBe(false)
  })

  it("rolling back one field keeps another edit that was made meanwhile", () => {
    let s = controlsReducer(loaded, { type: "edit", id: 1, category: "face", change: { style: "solid" } })
    s = controlsReducer(s, { type: "edit", id: 2, category: "face", change: { sensitivity: 0.9 } })
    s = controlsReducer(s, { type: "failed", id: 1, reason: "failed" })
    expect(s.categories.face.style).toBe("blur")
    expect(s.categories.face.sensitivity).toBe(0.9)
    expect(isSaving(s, "face")).toBe(true)
  })

  it("does not overwrite a newer edit with an older echo", () => {
    let s = controlsReducer(loaded, { type: "edit", id: 1, category: "face", change: { sensitivity: 0.6 } })
    s = controlsReducer(s, { type: "edit", id: 2, category: "face", change: { sensitivity: 0.9 } })
    s = controlsReducer(s, { type: "saved", id: 1, config: { categories: { face: cat({ sensitivity: 0.6 }) } } })
    expect(s.categories.face.sensitivity).toBe(0.9)
  })

  it("ignores edits to unknown categories and unknown ids", () => {
    expect(controlsReducer(loaded, { type: "edit", id: 1, category: "nope", change: { enabled: false } })).toBe(loaded)
    expect(controlsReducer(loaded, { type: "failed", id: 99, reason: "failed" })).toBe(loaded)
  })

  it("keeps showing the cards when a reload fails", () => {
    expect(controlsReducer(loaded, { type: "load-failed" }).status).toBe("ready")
    expect(controlsReducer(initialControlsState, { type: "load-failed" }).status).toBe("error")
  })

  it("tells a restart rule apart from a failure", () => {
    expect(failureReason(new ApiRequestError(409, "requires_restart"))).toBe("restart")
    expect(failureReason(new ApiRequestError(409, "conflict"))).toBe("failed")
    expect(failureReason(new ApiRequestError(422, "invalid_config"))).toBe("failed")
    expect(failureReason(new ApiRequestError(0, "network"))).toBe("failed")
  })
})
