/** REST client for the Streamify API. Only the API origin is contacted (docs/specs/app-web.md 4). */

import { API_URL } from "@/lib/config"
import type { ApiAlert } from "@/lib/live-state"
import type { ApiErrorBody, HoldSource, ModActionRequest, Wire, WhoAmI } from "@/lib/wire"

export interface HoldView {
  active: boolean
  sources: HoldSource[]
}

/** `POST /api/hold`. `engine:false` means no engine is confirming (HTTP 202): the command was published. */
export interface HoldResponse extends HoldView {
  engine: boolean
  published: number
}

export interface StatusResponse {
  engine: Wire<"EngineStatus"> | null
  engine_age_s: number | null
  hold: HoldView
  bus: { connected: boolean; contract_ok: boolean }
  session: { state: string; session_id: string | null }
}

/** An enrolled person as the API lists them (`EnrolledFaceMeta`): a label and times, never an image or embedding. */
export interface EnrolledFace {
  face_id: string
  label: string
  created_at: string
  expires_at: string | null
}

export interface EnrolList {
  faces: EnrolledFace[]
  max_faces: number
  ttl_s: number
  persisted: boolean
  min_frames: number
  max_frames: number
}

export interface CategorySettings {
  enabled: boolean
  style: "blur" | "pixelate" | "solid"
  sensitivity: number
  mask_when_unsure: boolean
  padding_px: number
}

/** The part of `GET /api/config` the controls screen uses. Everything else is ignored. */
export interface PublicConfig {
  profile?: string
  delay_seconds?: number
  categories: Record<string, CategorySettings>
  captions?: { enabled: boolean; target_lang: string }
  /** `languages` are the speech languages being checked; `supported_languages` is what can be chosen. */
  audio?: { languages: string[]; supported_languages?: string[] }
  secrets?: Record<string, "***" | null>
}

/** A JSON merge patch for `PATCH /api/config`. */
export type ConfigPatch = { categories?: Record<string, Partial<CategorySettings>> } & Record<string, unknown>

/** `GET /api/alerts`: alerts are category, time, confidence and duration only. */
export interface AlertsResponse {
  alerts: ApiAlert[]
  next_cursor: number
  count: number
}

export interface AlertsQuery {
  limit?: number
  modality?: string
  min_confidence?: number
}

const ENROL_TIMEOUT_MS = 30_000

export class ApiRequestError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string

  constructor(status: number, code: string, detail = "") {
    super(`${status} ${code}`)
    this.name = "ApiRequestError"
    this.status = status
    this.code = code
    this.detail = detail
  }

  /** Worth retrying: the request may not have reached the API. */
  get isNetwork(): boolean {
    return this.status === 0
  }
  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

export interface ApiClientOptions {
  baseUrl?: string
  getToken: () => string | null
  /** Called on every 401 so the app can drop the token and show the "open the link" screen. */
  onUnauthorized?: () => void
  fetchImpl?: typeof fetch
  timeoutMs?: number
}

export interface HoldOptions {
  /** `app` for the web app; the API records which source set a hold. */
  source?: "app" | "api"
  /** Streamer only: also clear holds set by other user sources (hotkey, chat). */
  releaseAll?: boolean
  signal?: AbortSignal
}

export function createApi(options: ApiClientOptions) {
  const baseUrl = (options.baseUrl ?? API_URL).replace(/\/+$/, "")
  const timeoutMs = options.timeoutMs ?? 5000

  async function request<T>(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal,
    timeout: number = timeoutMs,
  ): Promise<T> {
    const doFetch = options.fetchImpl ?? fetch
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeout)
    const onAbort = () => controller.abort()
    signal?.addEventListener("abort", onAbort)
    const headers: Record<string, string> = { Accept: "application/json" }
    const token = options.getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    const isForm = typeof FormData !== "undefined" && body instanceof FormData
    // A FormData body sets its own multipart Content-Type (with the boundary).
    if (body !== undefined && !isForm) headers["Content-Type"] = "application/json"
    let response: Response
    try {
      response = await doFetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
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
      const err = (payload ?? {}) as Partial<ApiErrorBody>
      if (response.status === 401) options.onUnauthorized?.()
      throw new ApiRequestError(response.status, err.error ?? "http_error", err.detail ?? "")
    }
    return payload as T
  }

  return {
    hold(active: boolean, opts: HoldOptions = {}): Promise<HoldResponse> {
      const body: Record<string, unknown> = { active, source: opts.source ?? "app" }
      if (opts.releaseAll) body.release_all = true
      return request<HoldResponse>("POST", "/api/hold", body, opts.signal)
    },
    getHold: (signal?: AbortSignal) => request<HoldView>("GET", "/api/hold", undefined, signal),
    getStatus: (signal?: AbortSignal) => request<StatusResponse>("GET", "/api/status", undefined, signal),

    // --- alert feed: the latest alerts, newest first (first load and after a reconnect) --------------
    getAlerts(query: AlertsQuery = {}, signal?: AbortSignal): Promise<AlertsResponse> {
      const params = new URLSearchParams({ order: "desc", limit: String(query.limit ?? 50) })
      if (query.modality) params.set("modality", query.modality)
      if (query.min_confidence !== undefined) params.set("min_confidence", String(query.min_confidence))
      return request<AlertsResponse>("GET", `/api/alerts?${params}`, undefined, signal)
    },

    // --- face allowlist (docs/specs/app-api.md 5.5). Embeddings never reach the browser. ----------
    getEnrolled: (signal?: AbortSignal) => request<EnrolList>("GET", "/api/enrol", undefined, signal),
    /** Upload the captured JPEG frames once. The caller drops its own copies right after. */
    enrol(label: string, frames: Blob[], signal?: AbortSignal): Promise<EnrolledFace> {
      const form = new FormData()
      form.append("label", label)
      frames.forEach((frame, i) => form.append("frames", frame, `frame-${i}.jpg`))
      return request<EnrolledFace>("POST", "/api/enrol", form, signal, ENROL_TIMEOUT_MS)
    },
    deleteEnrolled: (faceId: string, signal?: AbortSignal) =>
      request<{ deleted: true }>("DELETE", `/api/enrol/${encodeURIComponent(faceId)}`, undefined, signal),

    // --- who am I, and the moderator console ---------------------------------------------------------
    whoami: (signal?: AbortSignal) => request<WhoAmI>("GET", "/api/auth/whoami", undefined, signal),
    /** Streamer only. The returned token is a moderator link credential: show it once, never store it. */
    modInvite: (signal?: AbortSignal) =>
      request<{ token: string; role: "moderator"; expires_in: number }>("POST", "/api/auth/mod-invite", {}, signal),
    /** Moderator only. `{accepted:true}` means the chat shield was told; the outcome arrives on the WebSocket. */
    modAction: (action: ModActionRequest, signal?: AbortSignal) =>
      request<{ accepted: true; action: string }>("POST", "/api/mod/action", action, signal),

    // --- config (secrets arrive masked as "***" and are never rendered) ---------------------------
    getConfig: (signal?: AbortSignal) => request<PublicConfig>("GET", "/api/config", undefined, signal),
    patchConfig: (patch: ConfigPatch, signal?: AbortSignal) => request<PublicConfig>("PATCH", "/api/config", patch, signal),
  }
}

export type ApiClient = ReturnType<typeof createApi>
