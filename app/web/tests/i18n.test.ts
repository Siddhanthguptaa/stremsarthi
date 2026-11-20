import { describe, expect, it, vi } from "vitest"

import { en, hi, isLang, translate } from "@/lib/i18n"

describe("i18n", () => {
  it("has every English key in Hindi and nothing extra", () => {
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort())
  })

  it("has no empty strings", () => {
    for (const dict of [en, hi]) for (const value of Object.values(dict)) expect(value.trim()).not.toBe("")
  })

  it("uses the spec's Hindi labels", () => {
    expect(hi["action.hold"]).toBe("रोकें")
    expect(hi["action.resume"]).toBe("फिर शुरू करें")
    expect(hi["meter.risk"]).toBe("जोखिम मीटर")
    expect(hi["nav.report"]).toBe("सुरक्षा रिपोर्ट")
    expect(hi["chat.hidden"]).toBe("छिपे हुए संदेश")
  })

  it("translates and falls back to English with a warning", () => {
    expect(translate("hi", "action.hold")).toBe("रोकें")
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const original = (hi as Record<string, string>)["action.hold"]
    delete (hi as Record<string, string>)["action.hold"]
    try {
      expect(translate("hi", "action.hold")).toBe("Hold")
      expect(warn).toHaveBeenCalled()
    } finally {
      ;(hi as Record<string, string>)["action.hold"] = original
      warn.mockRestore()
    }
  })

  it("validates language codes", () => {
    expect(isLang("hi")).toBe(true)
    expect(isLang("fr")).toBe(false)
  })
})
