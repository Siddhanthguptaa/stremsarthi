/**
 * State of the /live dashboard apart from the hold button: engine status and the alert feed.
 *
 * Privacy: an alert keeps modality, category, action, time, confidence and the mask's duration only. The
 * bounding box, the event's free-form codes (`source`, `detail_code`) and everything else on a SafetyEvent
 * are dropped on arrival, whether it came over the WebSocket or from `GET /api/alerts`.
 */

import type { Category, EngineStatus, Modality, SafetyAction, SafetyEvent } from "@/lib/wire"

export const MAX_ALERTS = 200

export interface Alert {
  id: string
  /** ISO-8601 wall time from the event. */
  at: string
  modality: Modality
  category: Category
  action: SafetyAction
  /** 0..1 */
  confidence: number
  /** How long the mask or mute lasted; null until the engine reports the end. */
  durationMs: number | null
}

/** One row of `GET /api/alerts` (already reduced by the API). */
export interface ApiAlert {
  id: string
  at: string
  modality: Modality
  category: Category
  action: SafetyAction
  confidence: number
  duration_ms: number | null
}

/** Which alerts the feed shows. `guard` and `system` events only appear under "all". */
export type AlertFilter = "all" | "video" | "audio" | "chat"
export const ALERT_FILTERS: readonly AlertFilter[] = ["all", "video", "audio", "chat"]

export function filterAlerts(alerts: readonly Alert[], filter: AlertFilter): readonly Alert[] {
  return filter === "all" ? alerts : alerts.filter((a) => a.modality === filter)
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
  /** Alerts read over REST (first load, after a reconnect): adds the ones missed, never duplicates. */
  | { type: "backfill"; alerts: readonly ApiAlert[] }
  | { type: "clear-engine" }
  | { type: "api"; down: boolean }

export function toAlert(event: SafetyEvent): Alert {
  return {
    id: event.event_id,
    at: event.wall_ts,
    modality: event.modality,
    category: event.category,
    action: event.action,
    confidence: event.confidence,
    durationMs: event.duration_ms,
  }
}

export function fromApiAlert(a: ApiAlert): Alert {
  return {
    id: a.id,
    at: a.at,
    modality: a.modality,
    category: a.category,
    action: a.action,
    confidence: a.confidence,
    durationMs: a.duration_ms,
  }
}

const sameAlert = (a: Alert, b: Alert): boolean =>
  a.durationMs === b.durationMs && a.action === b.action && a.confidence === b.confidence

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
      // The engine republishes an event with its duration when the mask ends: update the row in place
      // (same position, not counted twice). A reconnect can also repeat an id: identical -> no change.
      const at = state.alerts.findIndex((a) => a.id === alert.id)
      if (at !== -1) {
        if (sameAlert(state.alerts[at], alert)) return state
        const alerts = state.alerts.slice()
        alerts[at] = alert
        return { ...state, alerts }
      }
      const alerts = [alert, ...state.alerts]
      if (alerts.length > MAX_ALERTS) alerts.length = MAX_ALERTS
      return { ...state, alerts, alertsTotal: state.alertsTotal + 1 }
    }
    case "backfill": {
      const known = new Set(state.alerts.map((a) => a.id))
      const added = event.alerts.filter((a) => !known.has(a.id)).map(fromApiAlert)
      if (added.length === 0) return state
      // Newest first by event time; the sort is stable so equal times keep their arrival order.
      const alerts = [...state.alerts, ...added].sort((x, y) => Date.parse(y.at) - Date.parse(x.at) || 0)
      if (alerts.length > MAX_ALERTS) alerts.length = MAX_ALERTS
      return { ...state, alerts, alertsTotal: state.alertsTotal + added.length }
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
 * is the time since the first non-live frame (0 while the latest emitted frame was live: engine/deadline.py
 * `_decide`, engine/publishers.py `observe`), so above 0 the stream is on the privacy slate.
 *
 * `lanes_stale` is NOT part of the rule: it also lists "audio" (the audio watchdog mutes audio while the masked
 * video is still live) and a video lane with no covering result while the engine is still LIVE inside its
 * coverage grace. Counting it made the chip say PROTECT over live masked video. Stale lanes are shown as a
 * quiet "still catching up" note instead (`catchingUpLanes`, and the engine metrics).
 */
export function chipFor(state: LiveState, held: boolean, now: number, everSeen: boolean): ChipKind {
  if (held) return "hold"
  if (!engineIsFresh(state, now)) return everSeen ? "offline" : "unknown"
  const engine = state.engine as EngineStatus
  switch (engine.state) {
    case "live":
      return engine.protect_seconds > 0 ? "protect" : "live"
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

/** Stale lanes while the stream is live (video or audio still catching up). Empty unless the chip is LIVE. */
export function catchingUpLanes(state: LiveState, chip: ChipKind): string[] {
  return chip === "live" && state.engine ? [...state.engine.lanes_stale] : []
}
