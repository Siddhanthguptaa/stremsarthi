/**
 * REST calls for the go-live, ingest and risk screens (docs/specs/app-web.md 5.1, app-api.md 5.x).
 *
 * Kept apart from `lib/api.ts` so the shared client stays as it is. Endpoints used:
 *   GET  /api/session            POST /api/session/start      POST /api/session/stop     (exist)
 *   GET  /api/pairing            GET  /api/pairing/{rtmp|srt}.png                       (T6.1, spec only)
 *   GET  /api/risk                                                                       (T6.1, spec only)
 * A call to an endpoint that is not there yet fails with 404 and the screens degrade (no QR, no meter
 * history); nothing here ever carries a stream key value, an image of the stream or a face.
 */

import { ApiRequestError, type ApiClientOptions } from "@/lib/api"
import { API_URL } from "@/lib/config"
import type { HoldSource, Wire } from "@/lib/wire"

export type SessionState = "idle" | "starting" | "waiting_ingest" | "live" | "stopping" | "ended" | "engine_lost" | "failed"
export type DestinationKind = "youtube" | "twitch"
export type Profile = "dev" | "demo"

/** `GET /api/session`. The ingest URLs are null until the go-live orchestration creates them. */
export interface SessionView {
  session_id: string | null
  state: SessionState
  started_at: string | null
  delay_seconds: number
  profile: Profile
  hold: { active: boolean; sources: HoldSource[] }
  ingest: { srt_url: string | null; rtmp_url: string | null }
}

export interface StartRequest {
  destinations: { kind: DestinationKind; enabled: boolean }[]
  delay_seconds?: number
  profile?: Profile
}

/** `POST /api/session/start`: which prerequisites are present (booleans, never values). */
export interface StartResponse {
  session_id: string
  state: string
  checks: { youtube_key: boolean; twitch_key: boolean; groq_key: boolean; sarvam_key: boolean; gpu: boolean }
}

/** `GET /api/pairing`. The destination stream key is never included. `key` is the per-session ingest key. */
export interface Pairing {
  rtmp: { url: string; key?: string }
  srt: { url: string }
  moblin_url: string
}

/** `GET /api/risk` as the spec words it (`ok|watch|warn|danger`). */
export interface RiskSnapshot {
  level: "ok" | "watch" | "warn" | "danger"
  score: number
  signals: Wire<"RiskSignal">["d"][]
}

export function createStreamerApi(options: ApiClientOptions) {
  const baseUrl = (options.baseUrl ?? API_URL).replace(/\/+$/, "")
  const timeoutMs = options.timeoutMs ?? 5000

  async function request(
    method: string,
    path: string,
    body: unknown,
    signal: AbortSignal | undefined,
    timeout: number,
  ): Promise<Response> {
    const doFetch = options.fetchImpl ?? fetch
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeout)
    const onAbort = () => controller.abort()
    signal?.addEventListener("abort", onAbort)
    const headers: Record<string, string> = {}
    const token = options.getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    if (body !== undefined) headers["Content-Type"] = "application/json"
    try {
      return await doFetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
      })
    } catch {
      throw new ApiRequestError(0, "network", "could not reach the API")
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener("abort", onAbort)
    }
  }

  async function failure(response: Response): Promise<ApiRequestError> {
    let payload: { error?: string; detail?: string } = {}
    try {
      payload = (await response.json()) as typeof payload
    } catch {
      /* empty or non-JSON body */
    }
    if (response.status === 401) options.onUnauthorized?.()
    return new ApiRequestError(response.status, payload.error ?? "http_error", payload.detail ?? "")
  }

  async function json<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await request(method, path, body, signal, timeoutMs)
    if (!response.ok) throw await failure(response)
    return (await response.json()) as T
  }

  return {
    getSession: (signal?: AbortSignal) => json<SessionView>("GET", "/api/session", undefined, signal),
    startSession: (body: StartRequest, signal?: AbortSignal) => json<StartResponse>("POST", "/api/session/start", body, signal),
    stopSession: (signal?: AbortSignal) => json<{ state: string }>("POST", "/api/session/stop", {}, signal),
    getPairing: (signal?: AbortSignal) => json<Pairing>("GET", "/api/pairing", undefined, signal),
    getRisk: (signal?: AbortSignal) => json<RiskSnapshot>("GET", "/api/risk", undefined, signal),
    /**
     * The Moblin QR code as a PNG blob (the endpoint needs the bearer token, so an <img src> cannot fetch it).
     * It encodes the ingest URL only, never a stream key or a picture of anything.
     */
    async getPairingQr(kind: "rtmp" | "srt", signal?: AbortSignal): Promise<Blob> {
      const response = await request("GET", `/api/pairing/${kind}.png`, undefined, signal, timeoutMs)
      if (!response.ok) throw await failure(response)
      return await response.blob()
    },
  }
}

export type StreamerApi = ReturnType<typeof createStreamerApi>
