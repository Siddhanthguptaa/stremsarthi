import { describe, expect, it } from "vitest"

import type { HoldResponse } from "@/lib/api"
import {
  holdReducer,
  type HoldEvent,
  type HoldState,
  holdView,
  initialHoldState,
  intentForPress,
  shouldHandleHoldKey,
} from "@/lib/hold-machine"
import type { HoldSource } from "@/lib/wire"

const run = (events: HoldEvent[], from: HoldState = initialHoldState): HoldState => events.reduce(holdReducer, from)
const echo = (source: HoldSource, active: boolean): HoldEvent => ({
  type: "echo",
  command: { source, active, actor: "abc" },
})
const reply = (sources: HoldSource[], engine = true): HoldResponse => ({
  active: sources.length > 0,
  sources,
  engine,
  published: 1,
})
const wsOpen: HoldEvent = { type: "connection", open: true }

describe("initial state", () => {
  it("is unknown until the server says something, and a tap holds", () => {
    const view = holdView(initialHoldState)
    expect(view.phase).toBe("unknown")
    expect(intentForPress(view)).toBe("hold")
  })

  it("a snapshot sets the state", () => {
    expect(holdView(run([{ type: "snapshot", sources: [] }])).phase).toBe("released")
    expect(holdView(run([{ type: "snapshot", sources: ["hotkey"] }])).phase).toBe("holding")
  })
})

describe("tap to hold (WebSocket open)", () => {
  it("shows sending, then follows the control.hold echo, not the tap", () => {
    let s = run([{ type: "snapshot", sources: [] }, wsOpen, { type: "request", active: true }])
    expect(holdView(s).phase).toBe("sending")
    expect(holdView(s).held).toBe(false) // optimistic UI is not used: nothing is shown as held yet

    s = run([{ type: "rest-ok", response: reply(["app"]) }], s)
    expect(holdView(s).phase).toBe("sending") // REST ok is not the confirmation while the socket is up
    s = run([echo("app", true)], s)
    expect(holdView(s)).toMatchObject({ phase: "holding", held: true, sources: ["app"], unconfirmed: false })
  })

  it("an echo that beats the REST reply also settles it", () => {
    const s = run([
      { type: "snapshot", sources: [] },
      wsOpen,
      { type: "request", active: true },
      echo("app", true),
      { type: "rest-ok", response: reply(["app"]) },
    ])
    expect(holdView(s).phase).toBe("holding")
    expect(s.pending).toBeNull()
  })

  it("a hold set by another source (hotkey, chat) shows up from its echo", () => {
    const s = run([{ type: "snapshot", sources: [] }, wsOpen, echo("hotkey", true)])
    expect(holdView(s)).toMatchObject({ phase: "holding", sources: ["hotkey"] })
    expect(run([echo("hotkey", false)], s).sources).toEqual([])
  })
})

describe("failures", () => {
  it("a failed request goes to failed with a retry that repeats the same action", () => {
    const s = run([{ type: "snapshot", sources: [] }, wsOpen, { type: "request", active: true }, { type: "rest-fail", code: "network" }])
    const view = holdView(s)
    expect(view.phase).toBe("failed")
    expect(view.error).toEqual({ code: "network", wanted: true })
    expect(view.held).toBe(false) // never shows a hold that was not confirmed
    expect(intentForPress(view)).toBe("retry-hold")

    const retried = run([{ type: "request", active: true }], s)
    expect(holdView(retried).phase).toBe("sending")
    expect(retried.error).toBeNull()
  })

  it("no confirmation within 2 s shows the failure and keeps listening for a late echo", () => {
    let s = run([{ type: "snapshot", sources: [] }, wsOpen, { type: "request", active: true }, { type: "timeout" }])
    expect(holdView(s)).toMatchObject({ phase: "failed", error: { code: "timeout", wanted: true } })
    s = run([echo("app", true)], s)
    expect(holdView(s)).toMatchObject({ phase: "holding", error: null })
  })

  it("REST ok but no echo in 2 s: shows the API's answer, flagged as unconfirmed", () => {
    const s = run([
      { type: "snapshot", sources: [] },
      wsOpen,
      { type: "request", active: true },
      { type: "rest-ok", response: reply(["app"], false) },
      { type: "timeout" },
    ])
    expect(holdView(s)).toMatchObject({ phase: "holding", held: true, unconfirmed: true })
    expect(holdView(run([echo("app", true)], s)).unconfirmed).toBe(false)
  })

  it("a late REST reply after a timeout clears the failure", () => {
    const s = run([
      { type: "snapshot", sources: [] },
      wsOpen,
      { type: "request", active: true },
      { type: "timeout" },
      { type: "rest-ok", response: reply(["app"]) },
      echo("app", true),
    ])
    expect(holdView(s)).toMatchObject({ phase: "holding", error: null })
  })

  it("a timeout with nothing pending does nothing", () => {
    const s = run([{ type: "snapshot", sources: [] }])
    expect(run([{ type: "timeout" }], s)).toBe(s)
  })
})

describe("works with the WebSocket down (REST only)", () => {
  it("the REST reply is the confirmation", () => {
    const s = run([
      { type: "snapshot", sources: [] },
      { type: "request", active: true },
      { type: "rest-ok", response: reply(["app"], false) },
    ])
    expect(holdView(s)).toMatchObject({ phase: "holding", held: true, unconfirmed: true })
  })

  it("resuming over REST only", () => {
    const s = run([
      { type: "snapshot", sources: ["app"] },
      { type: "request", active: false },
      { type: "rest-ok", response: reply([]) },
    ])
    expect(holdView(s)).toMatchObject({ phase: "released", held: false })
  })
})

describe("resume", () => {
  it("a tap while holding asks for confirmation (never releases directly)", () => {
    const view = holdView(run([{ type: "snapshot", sources: ["app"] }]))
    expect(intentForPress(view)).toBe("confirm-resume")
  })

  it("resume completes when every user source has cleared", () => {
    let s = run([{ type: "snapshot", sources: ["app", "hotkey", "chat"] }, wsOpen, { type: "request", active: false }])
    expect(holdView(s).phase).toBe("resuming")
    s = run([echo("app", false), echo("hotkey", false)], s)
    expect(holdView(s).phase).toBe("resuming") // chat still holds
    s = run([echo("chat", false)], s)
    expect(holdView(s)).toMatchObject({ phase: "released", held: false })
  })

  it("a failed resume retries as a resume", () => {
    const s = run([
      { type: "snapshot", sources: ["app"] },
      wsOpen,
      { type: "request", active: false },
      { type: "rest-fail", code: "rejected" },
    ])
    expect(holdView(s)).toMatchObject({ phase: "failed", held: true })
    expect(intentForPress(holdView(s))).toBe("retry-resume")
  })

  it("holds raised by the engine itself cannot be released from the app", () => {
    const s = run([{ type: "snapshot", sources: [] }, { type: "auto", sources: ["auto_egress"] }])
    const view = holdView(s)
    expect(view).toMatchObject({ phase: "holding", autoOnly: true })
    expect(intentForPress(view)).toBeNull()

    const both = run([echo("app", true)], s)
    expect(holdView(both)).toMatchObject({ autoOnly: false, sources: ["app", "auto_egress"] })
  })

  it("resume with an automatic hold still active completes once the user sources are gone", () => {
    const s = run([
      { type: "snapshot", sources: [] },
      { type: "auto", sources: ["auto_transport"] },
      echo("app", true),
      wsOpen,
      { type: "request", active: false },
      echo("app", false),
    ])
    expect(s.pending).toBeNull()
    expect(holdView(s)).toMatchObject({ phase: "holding", autoOnly: true })
  })

  it("auto sources replace only the automatic part and clear when the engine stops reporting them", () => {
    let s = run([{ type: "snapshot", sources: ["app"] }, { type: "auto", sources: ["auto_egress", "auto_transport"] }])
    expect(s.sources).toEqual(["app", "auto_egress", "auto_transport"])
    s = run([{ type: "auto", sources: [] }], s)
    expect(s.sources).toEqual(["app"])
  })
})

describe("in flight", () => {
  it("ignores taps while a request is in flight", () => {
    const s = run([{ type: "snapshot", sources: [] }, wsOpen, { type: "request", active: true }])
    expect(intentForPress(holdView(s))).toBeNull()
  })

  it("a snapshot taken before the request does not undo it", () => {
    const s = run([{ type: "snapshot", sources: [] }, wsOpen, { type: "request", active: true }, { type: "snapshot", sources: [] }])
    expect(holdView(s).phase).toBe("sending")
  })

  it("counts requests so the confirmation timer can be keyed on them", () => {
    const s = run([{ type: "request", active: true }, { type: "rest-ok", response: reply(["app"]) }, { type: "request", active: true }])
    expect(s.requestSeq).toBe(2)
  })

  it("rest events with no request pending are ignored", () => {
    const s = run([{ type: "snapshot", sources: [] }])
    expect(run([{ type: "rest-ok", response: reply(["app"]) }], s)).toBe(s)
    expect(run([{ type: "rest-fail", code: "network" }], s)).toBe(s)
  })

  it("never drops a hold echo: every echo in a burst is applied in order", () => {
    const events: HoldEvent[] = [{ type: "snapshot", sources: [] }, wsOpen]
    for (let i = 0; i < 500; i++) events.push(echo(i % 2 ? "app" : "hotkey", i % 3 !== 0))
    events.push(echo("api", true))
    expect(run(events).sources).toContain("api")
  })
})

describe("H key handling", () => {
  const key = (over: Partial<Parameters<typeof shouldHandleHoldKey>[0]> = {}) => ({ key: "h", ...over })

  it("accepts h and H", () => {
    expect(shouldHandleHoldKey(key(), false)).toBe(true)
    expect(shouldHandleHoldKey(key({ key: "H", shiftKey: true }), false)).toBe(true)
  })

  it("ignores other keys, repeats, modifiers and handled events", () => {
    expect(shouldHandleHoldKey(key({ key: "j" }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ repeat: true }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ ctrlKey: true }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ metaKey: true }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ altKey: true }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ defaultPrevented: true }), false)).toBe(false)
  })

  it("is focus safe: not while typing, not in a dialog", () => {
    for (const tagName of ["INPUT", "textarea", "SELECT"]) {
      expect(shouldHandleHoldKey(key({ target: { tagName } }), false)).toBe(false)
    }
    expect(shouldHandleHoldKey(key({ target: { tagName: "DIV", isContentEditable: true } }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ target: { tagName: "DIV", closest: () => ({}) } }), false)).toBe(false)
    expect(shouldHandleHoldKey(key({ target: { tagName: "BUTTON", closest: () => null } }), false)).toBe(true)
    expect(shouldHandleHoldKey(key(), true)).toBe(false) // the resume confirmation is open
  })
})
