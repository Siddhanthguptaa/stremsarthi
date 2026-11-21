/** Pure logic for the demo console's two preview panes (no React, no network, no timers). */

import type { DemoStatus, PreviewKind } from "@/lib/demo-api"

/** The preview token is replaced every 4 minutes by swapping the image URL. */
export const TOKEN_REFRESH_MS = 4 * 60_000
/** Never refresh faster than this, whatever `expires_in_s` says (protects the API from a tight loop). */
export const TOKEN_REFRESH_MIN_MS = 10_000
export const STATUS_POLL_MS = 4000
export const RETRY_BASE_MS = 1000
export const RETRY_MAX_MS = 15_000

/** Back-off for the n-th retry (0-based): 1, 2, 4, 8, 15, 15 ... seconds, with +-20 % jitter. */
export function retryDelayMs(attempt: number, random: () => number = Math.random): number {
  const exp = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** Math.max(0, Math.min(attempt, 10)))
  return Math.round(exp * (0.8 + 0.4 * random()))
}

/** When to ask for the next token: every 4 minutes, sooner only if the API says the token is shorter lived. */
export function tokenRefreshDelayMs(expiresInS: number): number {
  const margin = (Number.isFinite(expiresInS) ? expiresInS : 300) * 1000 - 20_000
  return Math.max(TOKEN_REFRESH_MIN_MS, Math.min(TOKEN_REFRESH_MS, margin))
}

/**
 * What a pane shows:
 *  - `connecting`   the status call has not answered yet
 *  - `disabled`     (raw only) the API has raw preview turned off: show how to enable it
 *  - `waiting`      the API is up but this stream has no frames yet (no session, or no source)
 *  - `unavailable`  (raw only) the status call failed, so raw preview cannot be assumed on
 *  - `stream`       mount the `<img>`
 */
export type PaneMode = "connecting" | "disabled" | "waiting" | "unavailable" | "stream"

export function paneMode(kind: PreviewKind, status: DemoStatus | null, statusFailed: boolean): PaneMode {
  if (status === null) {
    if (!statusFailed) return "connecting"
    // The status call failed: the protected stream is tried anyway (it is the one that matters),
    // the raw one is never assumed to be enabled.
    return kind === "protected" ? "stream" : "unavailable"
  }
  if (kind === "raw") {
    if (!status.raw_enabled) return "disabled"
    return status.raw_available ? "stream" : "waiting"
  }
  return status.protected_available ? "stream" : "waiting"
}

/** An image that errors while the viewer cap is reached is retried at this fixed pace (no growing back-off). */
export const FULL_RETRY_MS = 2000
/** A pane that has been "Connecting..." this long while the API says the stream exists is remounted. */
export const CONNECT_WATCHDOG_MS = 12_000

/** Is this stream at its viewer cap? Uses the per-stream count when the API gives one, else the total. */
export function viewerCapReached(kind: PreviewKind, status: DemoStatus): boolean {
  if (status.max_clients === null) return false
  const connected = status.clients_by_stream[kind] ?? status.clients
  return connected >= status.max_clients
}

export const streamAvailable = (kind: PreviewKind, status: DemoStatus): boolean =>
  kind === "raw" ? status.raw_enabled && status.raw_available : status.protected_available

/** The stream-state chip: HOLD wins, then the privacy slate, then LIVE; anything else shows the engine state. */
export type StageState = "live" | "hold" | "slate" | "other"

export function stageState(chip: string): StageState {
  if (chip === "hold") return "hold"
  if (chip === "protect") return "slate"
  if (chip === "live") return "live"
  return "other"
}
