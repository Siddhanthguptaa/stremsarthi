/**
 * HOLD button state machine (pure: no React, no timers, no network).
 *
 * Rules (docs/specs/app-web.md 5.2):
 * - One tap activates hold at once. Releasing is requested by the UI only after a confirmation.
 * - The state shown comes from the server: the `control.hold` echo on the WebSocket, or, when the
 *   socket is down, the REST reply (the button must work with REST alone).
 * - While a request is in flight the button says "sending". If the REST call fails, or nothing has
 *   confirmed after 2 s, the failure is shown with a retry. A retry is a new request: hold is idempotent.
 * - Hold is a set of sources (hotkey, api, chat, app, auto_transport, auto_egress). The UI is "holding"
 *   while any source holds. Resuming asks the API to clear every user source; the engine's own automatic
 *   holds (lost input or output) clear themselves and cannot be released from the app.
 */

import type { HoldResponse } from "@/lib/api"
import type { HoldCommand, HoldSource } from "@/lib/wire"

export const HOLD_CONFIRM_TIMEOUT_MS = 2000
export const AUTO_SOURCES: readonly HoldSource[] = ["auto_transport", "auto_egress"]

export interface Pending {
  /** What was asked for: true = hold, false = resume. */
  active: boolean
  /** The REST call has returned 2xx. The echo may still be on its way. */
  restOk: boolean
  /** The sources the API said would be held once the command applies. */
  restSources: HoldSource[] | null
}

export interface HoldFailure {
  code: "network" | "timeout" | "unauthorized" | "rejected"
  /** What the user was trying to do, so the retry repeats it. */
  wanted: boolean
}

export interface HoldState {
  /** null until the first snapshot, echo or REST reply tells us. */
  sources: HoldSource[] | null
  pending: Pending | null
  error: HoldFailure | null
  /** The state came from a REST reply and no echo or snapshot has confirmed it yet. */
  unconfirmed: boolean
  wsOpen: boolean
  /** Counts requests. The UI keys its 2 s confirmation timer on it, so a REST reply does not restart it. */
  requestSeq: number
}

export const initialHoldState: HoldState = {
  sources: null,
  pending: null,
  error: null,
  unconfirmed: false,
  wsOpen: false,
  requestSeq: 0,
}

export type HoldEvent =
  | { type: "connection"; open: boolean }
  | { type: "snapshot"; sources: HoldSource[] }
  | { type: "echo"; command: HoldCommand }
  /** Hold sources the engine reports itself (auto_transport / auto_egress), from engine.status. */
  | { type: "auto"; sources: HoldSource[] }
  | { type: "request"; active: boolean }
  | { type: "rest-ok"; response: HoldResponse }
  | { type: "rest-fail"; code: HoldFailure["code"] }
  | { type: "timeout" }
  | { type: "dismiss-error" }

export type HoldPhase = "unknown" | "released" | "holding" | "sending" | "resuming" | "failed"

export interface HoldView {
  phase: HoldPhase
  /** The stream is held by at least one source (confirmed state, ignoring a pending request). */
  held: boolean
  sources: HoldSource[]
  /** Held only by the engine's automatic sources: the app cannot release it. */
  autoOnly: boolean
  unconfirmed: boolean
  error: HoldFailure | null
}

export const isUserSource = (s: HoldSource): boolean => !AUTO_SOURCES.includes(s)
const sorted = (sources: Iterable<HoldSource>): HoldSource[] => [...new Set(sources)].sort()

function userSources(state: HoldState): HoldSource[] {
  return (state.sources ?? []).filter(isUserSource)
}

/** A pending request is satisfied when the confirmed state matches what it asked for. */
function satisfied(pending: Pending, sources: HoldSource[]): boolean {
  return pending.active ? sources.length > 0 : sources.filter(isUserSource).length === 0
}

function settle(state: HoldState, sources: HoldSource[], unconfirmed: boolean): HoldState {
  const pending = state.pending && satisfied(state.pending, sources) ? null : state.pending
  const cleared = pending === null
  return {
    ...state,
    sources,
    pending,
    error: cleared ? null : state.error,
    unconfirmed: cleared ? unconfirmed : state.unconfirmed,
  }
}

export function holdReducer(state: HoldState, event: HoldEvent): HoldState {
  switch (event.type) {
    case "connection":
      return { ...state, wsOpen: event.open }

    case "snapshot": {
      // A snapshot taken before our request must not undo it: ignore it while a request is in flight.
      if (state.pending) return state
      return { ...state, sources: sorted(event.sources), unconfirmed: false, error: null }
    }

    case "echo": {
      const next = new Set(state.sources ?? [])
      if (event.command.active) next.add(event.command.source)
      else next.delete(event.command.source)
      return settle(state, sorted(next), false)
    }

    case "auto": {
      const current = state.sources ?? []
      const merged = sorted([...current.filter(isUserSource), ...event.sources.filter((s) => !isUserSource(s))])
      if (merged.join() === current.join()) return state
      return settle(state, merged, state.unconfirmed)
    }

    case "request":
      return {
        ...state,
        pending: { active: event.active, restOk: false, restSources: null },
        error: null,
        requestSeq: state.requestSeq + 1,
      }

    case "rest-ok": {
      const pending = state.pending
      if (!pending) return state
      const restSources = sorted(event.response.sources)
      if (!state.wsOpen) {
        // REST-only mode: the reply is the confirmation.
        return settle({ ...state, pending: { ...pending, restOk: true, restSources } }, restSources, true)
      }
      return { ...state, pending: { ...pending, restOk: true, restSources }, error: null }
    }

    case "rest-fail":
      if (!state.pending) return state
      return { ...state, pending: null, error: { code: event.code, wanted: state.pending.active } }

    case "timeout": {
      const pending = state.pending
      if (!pending) return state
      if (pending.restOk && pending.restSources) {
        // The API accepted it but no echo arrived (engine or bus not confirming). Show what the API said,
        // marked as unconfirmed, so the button never lies about the request it just sent.
        return { ...state, sources: pending.restSources, pending: null, error: null, unconfirmed: true }
      }
      return { ...state, error: { code: "timeout", wanted: pending.active } }
    }

    case "dismiss-error":
      return { ...state, error: null }
  }
}

export function holdView(state: HoldState): HoldView {
  const sources = state.sources ?? []
  const held = sources.length > 0
  const autoOnly = held && userSources(state).length === 0
  let phase: HoldPhase
  if (state.error) phase = "failed"
  else if (state.pending) phase = state.pending.active ? "sending" : "resuming"
  else if (state.sources === null) phase = "unknown"
  else phase = held ? "holding" : "released"
  return { phase, held, sources, autoOnly, unconfirmed: state.unconfirmed, error: state.error }
}

/** What a tap on the button should do now. `null` = nothing (already in flight, or not releasable). */
export type HoldIntent = "hold" | "confirm-resume" | "retry-hold" | "retry-resume" | null

export function intentForPress(view: HoldView): HoldIntent {
  switch (view.phase) {
    case "unknown":
    case "released":
      return "hold"
    case "holding":
      return view.autoOnly ? null : "confirm-resume"
    case "failed":
      return view.error?.wanted ? "retry-hold" : "retry-resume"
    case "sending":
    case "resuming":
      return null
  }
}

// --- keyboard ----------------------------------------------------------------------------------------

export interface KeyEventLike {
  key: string
  repeat?: boolean
  defaultPrevented?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
  target?: unknown
}

interface ElementLike {
  tagName?: string
  isContentEditable?: boolean
  closest?: (selector: string) => unknown
}

const TEXT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"])

/**
 * Should the global `H` key toggle hold for this event? Not while the user types, not with a modifier
 * (browser shortcuts), not on key repeat, and not while a dialog is open (the resume confirmation).
 */
export function shouldHandleHoldKey(event: KeyEventLike, dialogOpen: boolean): boolean {
  if (event.key !== "h" && event.key !== "H") return false
  if (event.repeat || event.defaultPrevented || dialogOpen) return false
  if (event.ctrlKey || event.metaKey || event.altKey) return false
  const target = event.target as ElementLike | null | undefined
  if (target) {
    if (target.tagName && TEXT_TAGS.has(target.tagName.toUpperCase())) return false
    if (target.isContentEditable) return false
    if (typeof target.closest === "function" && target.closest('[contenteditable="true"],[role="textbox"],[role="combobox"]'))
      return false
  }
  return true
}
