/**
 * REST client for the judge-facing demo console (`/demo`).
 *
 *   POST /api/demo/preview-token        (streamer bearer) -> { token, expires_in_s }
 *   GET  /api/demo/status               -> { raw_enabled, protected_available, raw_available, clients }
 *   GET  /api/demo/preview/raw.mjpg?t=TOKEN        multipart MJPEG, the streamer's camera (loopback only, off by default)
 *   GET  /api/demo/preview/protected.mjpg?t=TOKEN  multipart MJPEG, what viewers get
 *
 * An `<img>` cannot send the bearer header, so the API hands out a short-lived preview token for the image URL.
 * The token value is only ever placed in an image URL: it is never rendered, logged or stored.
 */

import { ApiRequestError, type ApiClientOptions } from "@/lib/api"
import { API_URL } from "@/lib/config"

export type PreviewKind = "raw" | "protected"

export interface PreviewToken {
  token: string
  expires_in_s: number
}

export interface DemoStatus {
  /** `DEMO_RAW_PREVIEW=1` on the API, loopback clients only. Off by default for privacy. */
  raw_enabled: boolean
  protected_available: boolean
  raw_available: boolean
  /** Connected preview viewers, all streams together. */
  clients: number
  /** The same, per stream, when the API reports it as `{raw, protected}`. */
  clients_by_stream: Partial<Record<PreviewKind, number>>
  /** The viewer cap per stream (`null` when the API does not say). A full stream answers 503 `preview_clients_full`. */
  max_clients: number | null
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null
const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : null

/** Reads the status body defensively: a missing field is "not available", never an exception. */
export function parseDemoStatus(value: unknown): DemoStatus | null {
  if (!isRecord(value)) return null
  const by: Partial<Record<PreviewKind, number>> = {}
  let total = 0
  if (isRecord(value.clients)) {
    const raw = count(value.clients.raw)
    const protectedCount = count(value.clients.protected)
    if (raw !== null) by.raw = raw
    if (protectedCount !== null) by.protected = protectedCount
    total = (raw ?? 0) + (protectedCount ?? 0)
  } else {
    total = count(value.clients) ?? 0
  }
  const max = count(value.max_clients)
  return {
    raw_enabled: value.raw_enabled === true,
    protected_available: value.protected_available === true,
    raw_available: value.raw_available === true,
    clients: total,
    clients_by_stream: by,
    max_clients: max !== null && max > 0 ? max : null,
  }
}

export function parsePreviewToken(value: unknown): PreviewToken | null {
  if (!isRecord(value) || typeof value.token !== "string" || value.token === "") return null
  const expires = typeof value.expires_in_s === "number" && Number.isFinite(value.expires_in_s) ? value.expires_in_s : 300
  return { token: value.token, expires_in_s: expires }
}

/** The `<img src>` for one stream. The token is URL-encoded and is the only query parameter. */
export function previewUrl(kind: PreviewKind, token: string, baseUrl: string = API_URL): string {
  return `${baseUrl.replace(/\/+$/, "")}/api/demo/preview/${kind}.mjpg?t=${encodeURIComponent(token)}`
}

export function createDemoApi(options: ApiClientOptions) {
  const baseUrl = (options.baseUrl ?? API_URL).replace(/\/+$/, "")
  const timeoutMs = options.timeoutMs ?? 5000

  async function json(method: string, path: string, signal?: AbortSignal): Promise<unknown> {
    const doFetch = options.fetchImpl ?? fetch
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const onAbort = () => controller.abort()
    signal?.addEventListener("abort", onAbort)
    const headers: Record<string, string> = { Accept: "application/json" }
    const token = options.getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    let response: Response
    try {
      response = await doFetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: method === "POST" ? "{}" : undefined,
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
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      /* empty or non-JSON body */
    }
    if (!response.ok) {
      if (response.status === 401) options.onUnauthorized?.()
      const body = isRecord(payload) ? payload : {}
      throw new ApiRequestError(response.status, typeof body.error === "string" ? body.error : "http_error", "")
    }
    return payload
  }

  return {
    async getPreviewToken(signal?: AbortSignal): Promise<PreviewToken> {
      const parsed = parsePreviewToken(await json("POST", "/api/demo/preview-token", signal))
      if (!parsed) throw new ApiRequestError(502, "bad_token_reply")
      return parsed
    },
    async getStatus(signal?: AbortSignal): Promise<DemoStatus> {
      const parsed = parseDemoStatus(await json("GET", "/api/demo/status", signal))
      if (!parsed) throw new ApiRequestError(502, "bad_status_reply")
      return parsed
    },
    url: (kind: PreviewKind, token: string): string => previewUrl(kind, token, baseUrl),
  }
}

export type DemoApi = ReturnType<typeof createDemoApi>
