/**
 * State of the /live dashboard apart from the hold button: engine status and the alert feed.
 *
 * Privacy: an alert keeps category, action, time and confidence only. The bounding box, the event's
 * free-form codes (`source`, `detail_code`) and everything else on a SafetyEvent are dropped on arrival.
 */

import type { Category, EngineStatus, SafetyAction, SafetyEvent } from "@/lib/wire"

export const MAX_ALERTS = 200

export interface Alert {
  id: string
  /** ISO-8601 wall time from the event. */
  at: string
  category: Category
  action: SafetyAction
  /** 0..1 */
  confidence: number
}

export interface LiveState {
  engine: EngineStatus | null
  /** `Date.now()` of the last engine.status, for the staleness check. */
  engineAt: number | null
  alerts: Alert[]
  /** Alerts received in total (so the UI can say "showing the latest 200"). */
  alertsTotal: number
  /** The last REST call failed at the network level: the API process is not reachable. */
  apiDown: boolean
}

export const initialLiveState: LiveState = { engine: null, engineAt: null, alerts: [], alertsTotal: 0, apiDown: false }

export type LiveEvent =
  | { type: "engine"; status: EngineStatus; at: number }
  | { type: "safety"; event: SafetyEvent }
  | { type: "clear-engine" }
  | { type: "api"; down: boolean }

export function toAlert(event: SafetyEvent): Alert {
  return {
    id: event.event_id,
    at: event.wall_ts,
    category: event.category,
    action: event.action,
    confidence: event.confidence,
  }
}

export function liveReducer(state: LiveState, event: LiveEvent): LiveState {
  switch (event.type) {
    case "engine":
      return { ...state, engine: event.status, engineAt: event.at }
    case "api":
      return state.apiDown === event.down ? state : { ...state, apiDown: event.down }
    case "clear-engine":
      return state.engine === null ? state : { ...state, engine: null }
    case "safety": {
      const alert = toAlert(event.event)
      // The server replays nothing for safety.event, but a reconnect can overlap: ignore a repeated id.
      if (state.alerts.some((a) => a.id === alert.id)) return state
      const alerts = [alert, ...state.alerts]
      if (alerts.length > MAX_ALERTS) alerts.length = MAX_ALERTS
      return { ...state, alerts, alertsTotal: state.alertsTotal + 1 }
    }
  }
}

// --- the status chip -------------------------------------------------------------------------------------

export type ChipKind =
  | "hold"
  | "protect"
  | "live"
  | "starting"
  | "waiting"
  | "stopping"
  | "error"
  | "offline"
  | "unknown"

/** The engine is considered gone after 5 missed 1 Hz heartbeats. */
export const ENGINE_STALE_MS = 5000

export function engineIsFresh(state: LiveState, now: number): boolean {
  return state.engine !== null && state.engineAt !== null && now - state.engineAt <= ENGINE_STALE_MS
}

/**
 * LIVE / PROTECT / HOLD (docs/specs/app-web.md 5.1). Hold wins, then the engine's own state. `protect_seconds`
 * is the time since the first non-live frame (0 while every frame is checked), so above 0 the stream is on
 * the privacy slate.
 */
export function chipFor(state: LiveState, held: boolean, now: number, everSeen: boolean): ChipKind {
  if (held) return "hold"
  if (!engineIsFresh(state, now)) return everSeen ? "offline" : "unknown"
  const engine = state.engine as EngineStatus
  switch (engine.state) {
    case "live":
      return engine.protect_seconds > 0 || engine.lanes_stale.length > 0 ? "protect" : "live"
    case "starting":
    case "ready":
      return "starting"
    case "waiting_ingest":
      return "waiting"
    case "stopping":
      return "stopping"
    case "error":
      return "error"
  }
}
