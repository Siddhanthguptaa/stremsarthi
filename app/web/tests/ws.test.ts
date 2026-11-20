import { describe, expect, it, vi } from "vitest"

import { dumpsEnvelope, holdFrame, safetyFrame } from "./helpers"
import {
  backoffDelay,
  BACKOFF_MAX_MS,
  BACKOFF_MIN_MS,
  CLOSE_UNAUTHORIZED,
  RealtimeClient,
  SILENCE_LIMIT_MS,
  type SocketLike,
} from "@/lib/ws"

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = []
  readyState = 0
  onopen: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onclose: ((ev: { code: number }) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  sent: Array<Record<string, unknown>> = []
  closed = false
  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close() {
    this.closed = true
    this.readyState = 3
  }
  // --- test controls
  open() {
    this.readyState = 1
    this.onopen?.({})
  }
  recv(frame: unknown) {
    this.onmessage?.({ data: typeof frame === "string" ? frame : JSON.stringify(frame) })
  }
  drop(code = 1006) {
    this.readyState = 3
    this.onclose?.({ code })
  }
}

/** A manual timer queue so reconnects are deterministic. */
function timers() {
  const queue: Array<{ id: number; at: number; fn: () => void }> = []
  let id = 0
  let now = 0
  return {
    now: () => now,
    setTimer: (fn: () => void, ms: number) => {
      id += 1
      queue.push({ id, at: now + ms, fn })
      return id
    },
    clearTimer: (h: unknown) => {
      const i = queue.findIndex((t) => t.id === h)
      if (i >= 0) queue.splice(i, 1)
    },
    /** Advance time, firing due timers in order. Returns the delays that fired. */
    advance(ms: number) {
      const target = now + ms
      const fired: number[] = []
      for (;;) {
        queue.sort((a, b) => a.at - b.at)
        const next = queue[0]
        if (!next || next.at > target) break
        queue.shift()
        now = next.at
        fired.push(next.at)
        next.fn()
      }
      now = target
      return fired
    },
    pending: () => queue.length,
  }
}

function make(opts: { topics?: string[]; token?: string | null } = {}) {
  FakeSocket.instances = []
  const t = timers()
  const client = new RealtimeClient({
    topics: (opts.topics ?? ["control.hold", "engine.status"]) as never,
    getToken: () => (opts.token === undefined ? "tok-123" : opts.token),
    url: "ws://127.0.0.1:8000/api/ws",
    socketFactory: (url) => new FakeSocket(url),
    random: () => 0.5, // jitter factor 1.0
    setTimer: t.setTimer,
    clearTimer: t.clearTimer,
    now: t.now,
  })
  return { client, t, sockets: FakeSocket.instances }
}

const subscribed = (topics: string[]) => ({ type: "subscribed", topics })

describe("handshake", () => {
  it("sends the auth frame first, then subscribe, and never puts the token in the URL", () => {
    const { client, sockets } = make()
    client.start()
    expect(client.state).toBe("connecting")
    const s = sockets[0]
    expect(s.url).toBe("ws://127.0.0.1:8000/api/ws")
    expect(s.url).not.toContain("tok-123")
    s.open()
    expect(s.sent).toEqual([
      { type: "auth", token: "tok-123" },
      { type: "subscribe", topics: ["control.hold", "engine.status"] },
    ])
    s.recv(subscribed(["control.hold", "engine.status"]))
    expect(client.state).toBe("open")
  })

  it("sends an empty token when there is none (the server decides)", () => {
    const { client, sockets } = make({ token: null })
    client.start()
    sockets[0].open()
    expect(sockets[0].sent[0]).toEqual({ type: "auth", token: "" })
  })

  it("answers ping with pong", () => {
    const { client, sockets } = make()
    client.start()
    sockets[0].open()
    sockets[0].recv({ type: "ping" })
    expect(sockets[0].sent.at(-1)).toEqual({ type: "pong" })
  })

  it("ignores frames that are not valid wire data", () => {
    const { client, sockets } = make()
    const seen = vi.fn()
    client.onFrame(seen)
    client.start()
    sockets[0].open()
    sockets[0].recv("not json")
    sockets[0].recv({ topic: "control.hold", seq: 1, ts: "x", data: { t: "SafetyEvent", v: 1, d: {} } }) // wrong type for topic
    sockets[0].recv({ topic: "video.mask", seq: 1, ts: "x", data: {} }) // not a browser topic
    expect(seen).not.toHaveBeenCalled()
  })
})

describe("backoff and reconnect", () => {
  it("backoff doubles from 0.5 s to a cap of 8 s", () => {
    const seq = [0, 1, 2, 3, 4, 5, 9].map((n) => backoffDelay(n, () => 0.5))
    expect(seq).toEqual([500, 1000, 2000, 4000, 8000, 8000, 8000])
  })

  it("jitter stays within +/-20% and never exceeds 1.2x the cap", () => {
    for (let n = 0; n < 12; n++) {
      for (const r of [0, 0.25, 0.5, 0.75, 1]) {
        const d = backoffDelay(n, () => r)
        const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** n)
        expect(d).toBeGreaterThanOrEqual(Math.round(base * 0.8))
        expect(d).toBeLessThanOrEqual(Math.round(base * 1.2))
      }
    }
  })

  it("reconnects with the backoff sequence, then resubscribes and resets the sequence", () => {
    const { client, t, sockets } = make()
    client.start()
    sockets[0].open()
    sockets[0].recv(subscribed([]))
    expect(client.state).toBe("open")

    sockets[0].drop()
    expect(client.state).toBe("reconnecting")
    expect(sockets).toHaveLength(1)

    t.advance(499)
    expect(sockets).toHaveLength(1)
    t.advance(1) // 500 ms: first retry
    expect(sockets).toHaveLength(2)
    sockets[1].drop() // never opened
    t.advance(999)
    expect(sockets).toHaveLength(2)
    t.advance(1) // 1000 ms
    expect(sockets).toHaveLength(3)
    sockets[2].drop()
    t.advance(2000)
    expect(sockets).toHaveLength(4)

    sockets[3].open()
    expect(sockets[3].sent).toEqual([
      { type: "auth", token: "tok-123" },
      { type: "subscribe", topics: ["control.hold", "engine.status"] },
    ])
    sockets[3].recv(subscribed([]))
    expect(client.state).toBe("open")

    sockets[3].drop() // backoff starts over after a good connection
    t.advance(500)
    expect(sockets).toHaveLength(5)
  })

  it("a bad token (close 4401) stops reconnecting", () => {
    const { client, t, sockets } = make()
    client.start()
    sockets[0].open()
    sockets[0].drop(CLOSE_UNAUTHORIZED)
    expect(client.state).toBe("unauthorized")
    t.advance(60_000)
    expect(sockets).toHaveLength(1)
    expect(t.pending()).toBe(0)
  })

  it("stop() closes the socket and cancels the retry", () => {
    const { client, t, sockets } = make()
    client.start()
    sockets[0].open()
    sockets[0].drop()
    client.stop()
    t.advance(60_000)
    expect(sockets).toHaveLength(1)
    expect(client.state).toBe("idle")

    const second = make()
    second.client.start()
    second.sockets[0].open()
    second.client.stop()
    expect(second.sockets[0].closed).toBe(true)
    second.sockets[0].drop() // a late close event from the old socket is ignored
    second.t.advance(60_000)
    expect(second.sockets).toHaveLength(1)
  })

  it("a silent link (no frames, no pings) is dropped and reconnected", () => {
    const { client, t, sockets } = make()
    client.start()
    sockets[0].open()
    sockets[0].recv(subscribed([]))
    t.advance(SILENCE_LIMIT_MS - 1000)
    expect(sockets).toHaveLength(1)
    sockets[0].recv({ type: "ping" }) // any frame counts as life
    t.advance(SILENCE_LIMIT_MS - 1000)
    expect(sockets).toHaveLength(1)
    t.advance(10_000) // now silent for longer than the limit
    expect(sockets[0].closed).toBe(true)
    expect(client.state).toBe("reconnecting")
    t.advance(1000)
    expect(sockets).toHaveLength(2)
  })
})

describe("handlers", () => {
  it("delivers every frame to every handler, in order, and never drops control.hold", () => {
    const { client, sockets } = make()
    const a: string[] = []
    const b: string[] = []
    client.onFrame((f) => a.push(`${f.topic}:${f.seq}`))
    client.onFrame((f) => b.push(`${f.topic}:${f.seq}`))
    client.start()
    sockets[0].open()
    sockets[0].recv(subscribed([]))
    const sent: string[] = []
    for (let i = 1; i <= 1000; i++) {
      const frame = i % 50 === 0 ? holdFrame(i, "app", true) : safetyFrame(i)
      sent.push(`${frame.topic}:${i}`)
      sockets[0].recv(JSON.stringify(frame))
    }
    expect(a).toEqual(sent)
    expect(b).toEqual(sent)
    expect(a.filter((x) => x.startsWith("control.hold"))).toHaveLength(20)
  })

  it("a throwing handler does not starve the others", () => {
    const { client, sockets } = make()
    const seen: string[] = []
    client.onFrame(() => {
      throw new Error("boom")
    })
    client.onFrame((f) => seen.push(f.topic))
    client.start()
    sockets[0].open()
    sockets[0].recv(JSON.stringify(holdFrame(1, "hotkey", true)))
    expect(seen).toEqual(["control.hold"])
  })

  it("unregisters handlers: nothing is left after 100 mount/unmount cycles", () => {
    const { client } = make()
    for (let i = 0; i < 100; i++) {
      const offFrame = client.onFrame(() => {})
      const offState = client.onState(() => {})
      const offStore = client.subscribeState(() => {})
      expect(client.handlerCount).toBe(3)
      offFrame()
      offState()
      offStore()
    }
    expect(client.handlerCount).toBe(0)
  })

  it("an unregistered handler stops receiving frames", () => {
    const { client, sockets } = make()
    const seen = vi.fn()
    const off = client.onFrame(seen)
    client.start()
    sockets[0].open()
    sockets[0].recv(JSON.stringify(holdFrame(1, "app", true)))
    off()
    sockets[0].recv(JSON.stringify(holdFrame(2, "app", false)))
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it("reports state changes, once each", () => {
    const { client, sockets } = make()
    const states: string[] = []
    client.onState((s) => states.push(s))
    client.start()
    sockets[0].open()
    sockets[0].recv(subscribed([]))
    sockets[0].recv(subscribed([])) // repeated: no duplicate "open"
    sockets[0].drop()
    expect(states).toEqual(["idle", "connecting", "open", "reconnecting"])
  })
})

describe("test helpers", () => {
  it("build frames the real parser accepts", () => {
    expect(dumpsEnvelope("HoldCommand", { source: "app", active: true, actor: "a" })).toContain('"t":"HoldCommand"')
  })
})
