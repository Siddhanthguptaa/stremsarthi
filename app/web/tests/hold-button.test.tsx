import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

import { holdReducer, type HoldEvent, holdView, initialHoldState } from "@/lib/hold-machine"
import { LanguageProvider } from "@/components/shell/language-provider"
import { HoldButton } from "@/components/streamer/hold-button"

const run = (events: HoldEvent[]) => events.reduce(holdReducer, initialHoldState)

function render(events: HoldEvent[], over: { engineConfirmed?: boolean } = {}) {
  const view = holdView(run(events))
  return renderToStaticMarkup(
    <LanguageProvider>
      <HoldButton
        view={view}
        engineConfirmed={over.engineConfirmed ?? false}
        onPress={vi.fn()}
        confirmOpen={false}
        onConfirmOpenChange={vi.fn()}
        onConfirmResume={vi.fn()}
      />
    </LanguageProvider>,
  )
}

const released: HoldEvent[] = [{ type: "snapshot", sources: [] }]

describe("HoldButton markup", () => {
  it("is a button with the HOLD label in English and Hindi, 96 px tall, not pressed", () => {
    const html = render(released)
    expect(html).toContain("<button")
    expect(html).toContain("Hold")
    expect(html).toContain("रोकें")
    expect(html).toContain('aria-pressed="false"')
    expect(html).toContain("min-h-24") // 96 px
    expect(html).toContain("Press H to hold")
    expect(html).toContain('data-phase="released"')
  })

  it("shows holding by text and icon, pressed, and says it resumes on tap", () => {
    const html = render([{ type: "snapshot", sources: ["hotkey"] }])
    expect(html).toContain('aria-pressed="true"')
    expect(html).toContain("HOLDING")
    expect(html).toContain("रुका हुआ")
    expect(html).toContain("Tap to resume")
    expect(html).toContain("hotkey") // who holds
    expect(html).toContain("<svg") // an icon, not colour alone
  })

  it("announces hold changes assertively", () => {
    const html = render([{ type: "snapshot", sources: ["app"] }])
    expect(html).toMatch(/role="status"[^>]*aria-live="assertive"|aria-live="assertive"[^>]*role="status"/)
    expect(html).toContain("Stream is on hold")
  })

  it("shows sending while a request is in flight", () => {
    const html = render([...released, { type: "connection", open: true }, { type: "request", active: true }])
    expect(html).toContain("Sending…")
    expect(html).toContain('data-phase="sending"')
    expect(html).toContain('aria-disabled="true"')
  })

  it("turns into a retry on failure", () => {
    const html = render([
      ...released,
      { type: "connection", open: true },
      { type: "request", active: true },
      { type: "rest-fail", code: "network" },
    ])
    expect(html).toContain("Retry")
    expect(html).toContain("फिर कोशिश करें")
    expect(html).toContain("Not confirmed. Tap to retry.")
    expect(html).toContain('aria-disabled="false"')
  })

  it("explains an automatic hold the app cannot release", () => {
    const html = render([...released, { type: "auto", sources: ["auto_egress"] }])
    expect(html).toContain("Held automatically until the connection recovers.")
    expect(html).toContain('aria-disabled="true"')
  })

  it("marks a state the engine has not confirmed, and one it has", () => {
    const unconfirmed = render([
      ...released,
      { type: "request", active: true },
      { type: "rest-ok", response: { active: true, sources: ["app"], engine: false, published: 1 } },
    ])
    expect(unconfirmed).toContain("Sent. Waiting for the engine to confirm.")
    expect(render([{ type: "snapshot", sources: ["app"] }], { engineConfirmed: true })).toContain("Engine confirmed")
  })

  it("renders no image or canvas anywhere (privacy)", () => {
    for (const events of [released, [{ type: "snapshot", sources: ["app"] } as HoldEvent]]) {
      const html = render(events)
      expect(html).not.toMatch(/<img|<canvas|<video|data:image/)
    }
  })
})
