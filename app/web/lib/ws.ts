/**
 * Realtime client for `/api/ws` (docs/specs/app-api.md 5.3, docs/specs/app-web.md 5.3).
 *
 * - Native `WebSocket`. The first frame is `{type:"auth", token}` (never a URL query), then `subscribe`.
 * - Reconnects with exponential backoff 0.5 s to 8 s plus jitter, and resubscribes. After a reconnect the
 *   server replays the last `engine.status` and the current hold state.
 * - A handler is registered once and unregistered by the function `onFrame` returns, so a component that
 *   mounts and unmounts leaves nothing behind (PrivaStream's socket.ts leaked listeners).
 * - Nothing is dropped or coalesced here: every data frame reaches every handler, in order. `control.hold`
 *   in particular is never skipped. Rate control belongs to the consumer (the alert feed caps what it keeps).
 * - The socket is read-only apart from auth, subscribe and pong. Close code 4401 means the token is bad or
 *   expired: the client stops and reports `unauthorized` instead of retrying forever.
 */

import { WS_URL } from "@/lib/config"
import {
  type AnyWsFrame,
  type ClientFrame,
  parseWsFrame,
  type ReplayMarker,
  type WireTopic,
  type WsControl,
} from "@/lib/wire"

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "unauthorized"

export const CLOSE_UNAUTHORIZED = 4401
export const CLOSE_LAGGING = 4408
export const BACKOFF_MIN_MS = 500
export const BACKOFF_MAX_MS = 8000
/** The server pings every 15 s. No frame at all for this long means the link is dead (half-open TCP). */
export const SILENCE_LIMIT_MS = 40_000
const WATCHDOG_MS = 5000

/** Delay before reconnect attempt `attempt` (0-based): 0.5, 1, 2, 4, 8, 8... seconds, with +/-20% jitter. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** Math.max(0, attempt))
  const jitter = 0.8 + 0.4 * random()
  return Math.round(Math.min(BACKOFF_MAX_MS * 1.2, base * jitter))
}

/** The slice of `WebSocket` the client uses (so tests can inject a fake). */
export interface SocketLike {
  readyState: number
  onopen: ((ev: unknown) => void) | null
  onmessage: ((ev: { data: unknown }) => void) | null
  onclose: ((ev: { code: number }) => void) | null
  onerror: ((ev: unknown) => void) | null
  send(data: string): void
  close(code?: number): void
}
export type SocketFactory = (url: string) => SocketLike

export interface RealtimeOptions {
  topics: readonly WireTopic[]
  getToken: () => string | null
  url?: string
  socketFactory?: SocketFactory
  random?: () => number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  now?: () => number
}

const OPEN = 1

export class RealtimeClient {
  private readonly opts: RealtimeOptions
  private readonly frameHandlers = new Set<(frame: AnyWsFrame) => void>()
  private readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  private readonly replayHandlers = new Set<(marker: ReplayMarker) => void>()
  private socket: SocketLike | null = null
  private attempt = 0
  private retryTimer: unknown = null
  private watchdog: unknown = null
  private lastRx = 0
  private running = false
  private _state: ConnectionState = "idle"

  constructor(opts: RealtimeOptions) {
    this.opts = opts
  }

  get state(): ConnectionState {
    return this._state
  }

  /** Number of registered handlers (frames plus state). Zero after every consumer has unmounted. */
  get handlerCount(): number {
    return this.frameHandlers.size + this.stateHandlers.size + this.replayHandlers.size
  }

  /** Register a data-frame handler. Returns the function that removes it. */
  onFrame(handler: (frame: AnyWsFrame) => void): () => void {
    this.frameHandlers.add(handler)
    return () => {
      this.frameHandlers.delete(handler)
    }
  }

  /**
   * Register a replay-marker handler. The server sends a marker before it replays the current chat state to a
   * late joiner, in the same order as the frames, so a handler that resets its copy here never loses a frame.
   */
  onReplay(handler: (marker: ReplayMarker) => void): () => void {
    this.replayHandlers.add(handler)
    return () => {
      this.replayHandlers.delete(handler)
    }
  }

  /** `useSyncExternalStore` pair: `subscribeState` does not call the listener on subscribe. */
  readonly subscribeState = (listener: () => void): (() => void) => {
    const handler = () => listener()
    this.stateHandlers.add(handler)
    return () => {
      this.stateHandlers.delete(handler)
    }
  }
  readonly getStateSnapshot = (): ConnectionState => this._state

  /** Register a connection-state handler (called immediately with the current state). */
  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    handler(this._state)
    return () => {
      this.stateHandlers.delete(handler)
    }
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.attempt = 0
    this.connect()
  }

  stop(): void {
    this.running = false
    this.clearTimers()
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
      try {
        socket.close(1000)
      } catch {
        /* already closed */
      }
    }
    this.setState("idle")
  }

  // --- internals -------------------------------------------------------------------------------

  private setState(next: ConnectionState): void {
    if (this._state === next) return
    this._state = next
    for (const handler of [...this.stateHandlers]) handler(next)
  }

  private timer(fn: () => void, ms: number): unknown {
    return (this.opts.setTimer ?? ((f, m) => setTimeout(f, m)))(fn, ms)
  }
  private untimer(handle: unknown): void {
    if (handle === null) return
    ;(this.opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>)))(handle)
  }
  private now(): number {
    return (this.opts.now ?? Date.now)()
  }

  private clearTimers(): void {
    this.untimer(this.retryTimer)
    this.untimer(this.watchdog)
    this.retryTimer = this.watchdog = null
  }

  private send(frame: ClientFrame): void {
    if (this.socket && this.socket.readyState === OPEN) this.socket.send(JSON.stringify(frame))
  }

  private connect(): void {
    if (!this.running) return
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting")
    const factory = this.opts.socketFactory ?? ((url: string) => new WebSocket(url) as unknown as SocketLike)
    let socket: SocketLike
    try {
      socket = factory(this.opts.url ?? WS_URL)
    } catch {
      this.scheduleRetry()
      return
    }
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.lastRx = this.now()
      // Auth is always the first frame. With no token the server decides (it accepts "" only when
      // REQUIRE_AUTH is off, local development).
      this.send({ type: "auth", token: this.opts.getToken() ?? "" })
      this.send({ type: "subscribe", topics: [...this.opts.topics] })
      this.armWatchdog()
    }
    socket.onmessage = (ev) => {
      if (this.socket !== socket) return
      this.lastRx = this.now()
      if (typeof ev.data !== "string") return
      const parsed = parseWsFrame(ev.data)
      if (!parsed) return
      if ("type" in parsed) {
        this.onControl(parsed)
        return
      }
      for (const handler of [...this.frameHandlers]) {
        try {
          handler(parsed)
        } catch {
          /* one failing consumer must not starve the others, least of all the hold handler */
        }
      }
    }
    socket.onerror = () => {
      /* onclose follows; reconnect is handled there */
    }
    socket.onclose = (ev) => {
      if (this.socket !== socket) return
      this.socket = null
      this.untimer(this.watchdog)
      this.watchdog = null
      if (!this.running) return
      if (ev.code === CLOSE_UNAUTHORIZED) {
        this.running = false
        this.setState("unauthorized")
        return
      }
      this.scheduleRetry()
    }
  }

  private onControl(msg: WsControl): void {
    if (msg.type === "ping") {
      this.send({ type: "pong" })
    } else if (msg.type === "subscribed") {
      this.attempt = 0
      this.setState("open")
    } else if (msg.type === "replay") {
      for (const handler of [...this.replayHandlers]) {
        try {
          handler(msg)
        } catch {
          /* a failing consumer must not stop the replayed frames that follow */
        }
      }
    }
    // `error` frames (forbidden topic, unknown topic) are a programming error: nothing to retry.
  }

  private scheduleRetry(): void {
    if (!this.running) return
    this.setState("reconnecting")
    const delay = backoffDelay(this.attempt, this.opts.random)
    this.attempt += 1
    this.retryTimer = this.timer(() => {
      this.retryTimer = null
      this.connect()
    }, delay)
  }

  private armWatchdog(): void {
    this.untimer(this.watchdog)
    this.watchdog = this.timer(() => {
      this.watchdog = null
      const socket = this.socket
      if (!socket || !this.running) return
      if (this.now() - this.lastRx > SILENCE_LIMIT_MS) {
        // No frame, not even a ping: drop the link and reconnect.
        socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
        this.socket = null
        try {
          socket.close()
        } catch {
          /* ignore */
        }
        this.scheduleRetry()
      } else {
        this.armWatchdog()
      }
    }, WATCHDOG_MS)
  }
}
