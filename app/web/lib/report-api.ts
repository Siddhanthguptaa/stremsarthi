/**
 * Report endpoint client (kept apart from `lib/api.ts`, which the streamer app owns).
 *
 * Endpoints (app/api/routes/report.py; streamer and viewer-readonly tokens read, moderator and hotkey get 403):
 *   GET /api/report                          -> { sessions: [id, ...] }, oldest first, the live session last
 *   GET /api/report/{session_id}             -> report JSON, schema_version 1 (ended_at null while live)
 *   GET /api/report/{session_id}/export?format=json|html -> a download (Content-Disposition: attachment)
 *   401 bad token. 403 wrong role. 404 unknown or expired session. 422 bad format.
 * Errors use the API body `{ "error": code, "detail": str }`. The report is passed through `parseReport`.
 */

import { API_URL } from "@/lib/config"
import { parseReport, ReportShapeError, type SafetyReport } from "@/lib/report-types"

export class ReportApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string) {
    super(`${status} ${code}`)
    this.name = "ReportApiError"
    this.status = status
    this.code = code
  }
}

export interface ReportClientOptions {
  baseUrl?: string
  getToken: () => string | null
  onUnauthorized?: () => void
  fetchImpl?: typeof fetch
  timeoutMs?: number
}

/** Session ids are `secrets.token_urlsafe(9)`: letters, digits, `-`, `_`. Anything else never leaves the browser. */
export const SESSION_ID_RE = /^[A-Za-z0-9_-]{1,64}$/

export function createReportClient(options: ReportClientOptions) {
  const baseUrl = (options.baseUrl ?? API_URL).replace(/\/+$/, "")
  const timeoutMs = options.timeoutMs ?? 15_000

  async function send(path: string, accept: string, signal?: AbortSignal): Promise<Response> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const onAbort = () => controller.abort()
    signal?.addEventListener("abort", onAbort)
    const headers: Record<string, string> = { Accept: accept }
    const token = options.getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    let response: Response
    try {
      response = await (options.fetchImpl ?? fetch)(`${baseUrl}${path}`, {
        method: "GET",
        headers,
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
      })
    } catch {
      throw new ReportApiError(0, "network")
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener("abort", onAbort)
    }
    if (!response.ok) {
      if (response.status === 401) options.onUnauthorized?.()
      let code: unknown
      try {
        code = ((await response.json()) as { error?: unknown } | null)?.error
      } catch {
        /* empty or non-JSON body */
      }
      throw new ReportApiError(response.status, typeof code === "string" ? code.slice(0, 40) : "http_error")
    }
    return response
  }

  const checkId = (sessionId: string) => {
    if (!SESSION_ID_RE.test(sessionId)) throw new ReportApiError(404, "not_found")
  }

  return {
    async getReport(sessionId: string, signal?: AbortSignal): Promise<SafetyReport> {
      checkId(sessionId)
      const response = await send(`/api/report/${encodeURIComponent(sessionId)}`, "application/json", signal)
      try {
        return parseReport(await response.json())
      } catch (err) {
        if (err instanceof ReportShapeError || err instanceof SyntaxError) throw new ReportApiError(502, "bad_report")
        throw err
      }
    },

    /** Known session ids, oldest first (the live session, if any, is last). */
    async listSessions(signal?: AbortSignal): Promise<string[]> {
      const response = await send("/api/report", "application/json", signal)
      let body: unknown
      try {
        body = await response.json()
      } catch {
        throw new ReportApiError(502, "bad_report")
      }
      const list = (body as { sessions?: unknown } | null)?.sessions
      return Array.isArray(list) ? list.filter((id): id is string => typeof id === "string" && SESSION_ID_RE.test(id)) : []
    },

    /** The server's own download (`/export?format=`), fetched with the token so it can be saved as a file. */
    async exportReport(sessionId: string, format: ExportFormat, signal?: AbortSignal): Promise<Blob> {
      checkId(sessionId)
      const accept = format === "html" ? "text/html" : "application/json"
      const response = await send(`/api/report/${encodeURIComponent(sessionId)}/export?format=${format}`, accept, signal)
      return response.blob()
    },
  }
}

export type ExportFormat = "json" | "html"

/** The most recent session id from `listSessions`, or null. */
export const latestSessionId = (ids: readonly string[]): string | null => (ids.length ? ids[ids.length - 1] : null)

export type ReportClient = ReturnType<typeof createReportClient>

/** `stream-sarthi-report-<id>.json`: the id is already restricted to safe characters. */
export const reportFileName = (sessionId: string, format: ExportFormat = "json"): string =>
  `stream-sarthi-report-${sessionId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || "session"}.${format}`

/** The sanitised report as pretty JSON. Only what `parseReport` kept is in it. */
export const reportToJson = (report: SafetyReport): string => JSON.stringify(report, null, 2)

/** Save a blob on this device (a Blob URL, revoked right away). Nothing is uploaded. */
export function saveBlob(blob: Blob, fileName: string, doc: Document = document): void {
  const url = URL.createObjectURL(blob)
  const a = doc.createElement("a")
  a.href = url
  a.download = fileName
  a.rel = "noopener"
  doc.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/** Fallback when the server cannot be reached: save the cleaned report this page already holds. */
export function downloadReportJson(report: SafetyReport, doc: Document = document): void {
  saveBlob(new Blob([reportToJson(report)], { type: "application/json" }), reportFileName(report.session_id), doc)
}
