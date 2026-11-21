/** Logic for the go-live screen: setup form state, readiness, and API failures as message keys. */

import type { DestinationKind, Profile, SessionState, StartRequest } from "@/lib/api-streamer"
import { ApiRequestError, type PublicConfig } from "@/lib/api"
import type { I18nKey } from "@/lib/i18n"

export const DELAY_MIN = 3.0
export const DELAY_MAX = 5.0
export const DELAY_STEP = 0.5
export const DEFAULT_DELAY = 4.0
export const DESTINATIONS: readonly DestinationKind[] = ["youtube", "twitch"]
export const PROFILES: readonly Profile[] = ["dev", "demo"]

const SECRET_FOR: Record<DestinationKind, string> = { youtube: "youtube_stream_key", twitch: "twitch_stream_key" }

/** Whether a stream key is present in `.env`. The API only ever says `"***"` or null: the value is never seen. */
export function keyConfigured(config: PublicConfig | null, kind: DestinationKind): boolean {
  return config?.secrets?.[SECRET_FOR[kind]] === "***"
}

export interface SetupForm {
  destinations: Record<DestinationKind, boolean>
  delay: number
  profile: Profile
}

/** Initial form from `GET /api/config`: the saved destinations, delay and profile. */
export function formFromConfig(config: PublicConfig | null): SetupForm {
  const raw = (config as (PublicConfig & { destinations?: { kind: string; enabled: boolean }[] }) | null)?.destinations
  const enabled = (kind: DestinationKind) => raw?.find((d) => d.kind === kind)?.enabled ?? kind === "youtube"
  return {
    destinations: { youtube: enabled("youtube"), twitch: enabled("twitch") },
    delay: clampDelay(config?.delay_seconds ?? DEFAULT_DELAY),
    profile: config?.profile === "demo" ? "demo" : "dev",
  }
}

export function clampDelay(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_DELAY
  const stepped = Math.round(value / DELAY_STEP) * DELAY_STEP
  return Math.min(DELAY_MAX, Math.max(DELAY_MIN, stepped))
}

export type Readiness = { ok: true } | { ok: false; reason: "no_destination" | "missing_keys"; missing: DestinationKind[] }

/** The same rules the API applies on start, so the button can say why it is disabled. */
export function readiness(form: SetupForm, config: PublicConfig | null): Readiness {
  const wanted = DESTINATIONS.filter((k) => form.destinations[k])
  if (wanted.length === 0) return { ok: false, reason: "no_destination", missing: [] }
  const missing = wanted.filter((k) => !keyConfigured(config, k))
  if (missing.length > 0) return { ok: false, reason: "missing_keys", missing }
  return { ok: true }
}

export function buildStartRequest(form: SetupForm): StartRequest {
  return {
    destinations: DESTINATIONS.map((kind) => ({ kind, enabled: form.destinations[kind] })),
    delay_seconds: form.delay,
    profile: form.profile,
  }
}

const ACTIVE: readonly SessionState[] = ["starting", "waiting_ingest", "live", "stopping"]
/** Delay and profile cannot change while a session runs (the API answers 409). */
export const isActiveState = (state: SessionState | null): boolean => state !== null && ACTIVE.includes(state)

export const SESSION_STATE_KEY: Record<SessionState, I18nKey> = {
  idle: "golive.state.idle",
  starting: "golive.state.starting",
  waiting_ingest: "golive.state.waiting_ingest",
  live: "golive.state.live",
  stopping: "golive.state.stopping",
  ended: "golive.state.ended",
  engine_lost: "golive.state.engine_lost",
  failed: "golive.state.failed",
}

/** A failed start or stop as a message the streamer can act on. `detail` never carries a key value. */
export function sessionErrorKey(error: unknown): I18nKey {
  if (!(error instanceof ApiRequestError)) return "golive.err.generic"
  if (error.isNetwork) return "golive.err.network"
  if (error.isUnauthorized) return "golive.err.unauthorized"
  switch (error.code) {
    case "session_active":
      return "golive.err.session_active"
    case "no_destination":
      return "golive.err.no_destination"
    case "missing_keys":
      return "golive.err.missing_keys"
    case "bus_unavailable":
      return "golive.err.bus"
    case "requires_restart":
      return "golive.err.restart"
    default:
      return "golive.err.generic"
  }
}

/** Setup checks to show after a start (booleans from the API). */
export const CHECK_LABELS = [
  ["youtube_key", "golive.check.youtube_key"],
  ["twitch_key", "golive.check.twitch_key"],
  ["groq_key", "golive.check.groq_key"],
  ["sarvam_key", "golive.check.sarvam_key"],
  ["gpu", "golive.check.gpu"],
] as const satisfies readonly (readonly [string, I18nKey])[]
