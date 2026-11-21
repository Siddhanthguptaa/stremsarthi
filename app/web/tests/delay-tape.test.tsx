// @vitest-environment jsdom
import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { DelayTape, DEFAULT_DELAY_S, countByModality, tapeDelay, tapeEvents, tapePosition, tapeWindow } from "@/components/streamer/delay-tape"
import type { Alert } from "@/lib/live-state"
import { renderIn } from "./dom-helpers"

const NOW = Date.parse("2026-01-01T10:00:20Z")
const at = (agoS: number) => new Date(NOW - agoS * 1000).toISOString()
const alert = (id: string, agoS: number, over: Partial<Alert> = {}): Alert => ({
  id,
  at: at(agoS),
  modality: "video",
  category: "face",
  action: "masked",
  confidence: 0.9,
  durationMs: null,
  ...over,
})

describe("delay tape maths", () => {
  it("uses the engine delay when sane, else 4 s", () => {
    expect(tapeDelay(4.5)).toBe(4.5)
    expect(tapeDelay(null)).toBe(DEFAULT_DELAY_S)
    expect(tapeDelay(0)).toBe(DEFAULT_DELAY_S)
    expect(tapeDelay(NaN)).toBe(DEFAULT_DELAY_S)
  })

  it("is 10 s long, longer for a long delay", () => {
    expect(tapeWindow(4)).toBe(10)
    expect(tapeWindow(9)).toBe(12)
  })

  it("places age 0 at the left edge and clamps to the track", () => {
    expect(tapePosition(0, 10)).toBe(0)
    expect(tapePosition(5, 10)).toBe(50)
    expect(tapePosition(-3, 10)).toBe(0)
    expect(tapePosition(30, 10)).toBe(100)
  })

  it("keeps only events inside the window and positions them by age", () => {
    const events = tapeEvents([alert("a", 0), alert("b", 4), alert("c", 9.5), alert("d", 11)], NOW, 10)
    expect(events.map((e) => e.id)).toEqual(["a", "b", "c"])
    expect(events[0].left).toBe(0)
    expect(events[1].left).toBe(40)
    expect(events[2].left).toBeCloseTo(95)
  })

  it("treats a future timestamp as detected now and ignores guard, system and bad times", () => {
    const events = tapeEvents([alert("f", -2), alert("g", 1, { modality: "guard" }), alert("s", 1, { modality: "system" }), alert("x", 1, { at: "nope" })], NOW, 10)
    expect(events.map((e) => e.id)).toEqual(["f"])
    expect(events[0].left).toBe(0)
  })

  it("never lets a block run past the right edge", () => {
    const [e] = tapeEvents([alert("w", 9, { durationMs: 5000 })], NOW, 10)
    expect(e.left + e.width).toBeLessThanOrEqual(100)
  })

  it("counts by modality", () => {
    const events = tapeEvents([alert("a", 1), alert("b", 2, { modality: "audio" }), alert("c", 3, { modality: "chat" }), alert("d", 3, { modality: "audio" })], NOW, 10)
    expect(countByModality(events)).toEqual({ video: 1, audio: 2, chat: 1 })
  })
})

describe("DelayTape", () => {
  it("shows a calm line when nothing was masked", () => {
    renderIn(<DelayTape alerts={[]} now={NOW} delayS={4} />)
    expect(screen.getByText("Nothing masked in the last 10 seconds")).toBeTruthy()
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("0 masked")
  })

  it("draws one mask per recent event and describes them", () => {
    renderIn(<DelayTape alerts={[alert("a", 1), alert("b", 2, { modality: "audio" })]} now={NOW} delayS={4} />)
    expect(screen.getAllByTestId("tape-mask")).toHaveLength(2)
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("2 masked (1 on screen, 1 audio, 0 chat)")
    expect(screen.getAllByText("On air").length).toBeGreaterThan(0)
  })
})
