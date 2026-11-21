import { describe, expect, it } from "vitest"

import {
  buildCaptionsPatch,
  captionReducer,
  captionsFromConfig,
  CAPTION_LANGUAGES,
  initialCaptions,
  isCaptionLanguage,
  MAX_CAPTION_LINES,
} from "@/lib/captions"

const event = (start: number, text: string, final = true) => ({ pts_start: start, pts_end: start + 1, lang: "hi", text, is_final: final })

describe("captions settings", () => {
  it("builds merge patches with the wire key target_lang", () => {
    expect(buildCaptionsPatch({ enabled: true })).toEqual({ captions: { enabled: true } })
    expect(buildCaptionsPatch({ language: "ta" })).toEqual({ captions: { target_lang: "ta" } })
  })
  it("reads the saved setting and defaults to off and Hindi", () => {
    expect(captionsFromConfig({ captions: { enabled: true, target_lang: "kn" } })).toEqual({ enabled: true, language: "kn" })
    expect(captionsFromConfig(null)).toEqual({ enabled: false, language: "hi" })
  })
  it("offers Hindi first and the Indian languages the service supports", () => {
    expect(CAPTION_LANGUAGES[0]).toBe("hi")
    expect(isCaptionLanguage("ta")).toBe(true)
    expect(isCaptionLanguage("xx")).toBe(false)
  })
})

describe("captionReducer", () => {
  it("updates a partial line in place and keeps only the last few lines", () => {
    let s = captionReducer(initialCaptions, { type: "caption", event: event(1, "नम", false) })
    s = captionReducer(s, { type: "caption", event: event(1, "नमस्ते", true) })
    expect(s.lines).toEqual([{ id: "1", text: "नमस्ते", lang: "hi", final: true }])
    for (let i = 2; i < 10; i++) s = captionReducer(s, { type: "caption", event: event(i, `line ${i}`) })
    expect(s.lines).toHaveLength(MAX_CAPTION_LINES)
    expect(s.lines.at(-1)?.text).toBe("line 9")
  })
  it("clears", () => {
    const s = captionReducer(initialCaptions, { type: "caption", event: event(1, "x") })
    expect(captionReducer(s, { type: "clear" })).toBe(initialCaptions)
  })
})
