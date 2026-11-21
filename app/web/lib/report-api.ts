/**
 * Report endpoint client (kept apart from `lib/api.ts`, which the streamer app owns).
 *
 * Assumed endpoint (docs/specs/app-api.md 5.2 and 5.9; being added in app/api by another agent):
 *   GET /api/report/{session_id}   Authorization: Bearer <streamer or viewer-readonly token>
 *   200 -> report JSON, schema_version 1.   401 bad token. 403 wrong role. 404 unknown or expired session.
 * Errors use the API body `{ "error": code, "detail": str }`. The response is passed through `parseReport`.
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

  return {
    async getReport(sessionId: string, signal?: AbortSignal): Promise<SafetyReport> {
      if (!SESSION_ID_RE.test(sessionId)) throw new ReportApiError(404, "not_found")
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), timeoutMs)
      const onAbort = () => controller.abort()
      signal?.addEventListener("abort", onAbort)
      const headers: Record<string, string> = { Accept: "application/json" }
      const token = options.getToken()
      if (token) headers.Authorization = `Bearer ${token}`
      let response: Response
      try {
        response = await (options.fetchImpl ?? fetch)(`${baseUrl}/api/report/${encodeURIComponent(sessionId)}`, {
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
      let payload: unknown = null
      try {
        payload = await response.json()
      } catch {
        /* empty or non-JSON body */
      }
      if (!response.ok) {
        if (response.status === 401) options.onUnauthorized?.()
        const code = (payload as { error?: unknown } | null)?.error
        throw new ReportApiError(response.status, typeof code === "string" ? code.slice(0, 40) : "http_error")
      }
      try {
        return parseReport(payload)
      } catch (err) {
        if (err instanceof ReportShapeError) throw new ReportApiError(502, "bad_report")
        throw err
      }
    },
  }
}

export type ReportClient = ReturnType<typeof createReportClient>

/** `stream-sarthi-report-<id>.json`: the id is already restricted to safe characters. */
export const reportFileName = (sessionId: string): string =>
  `stream-sarthi-report-${sessionId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || "session"}.json`

/** The sanitised report as pretty JSON. Only what `parseReport` kept is in it. */
export const reportToJson = (report: SafetyReport): string => JSON.stringify(report, null, 2)

/** Save the report on this device (a Blob URL, revoked right away). Nothing is uploaded. */
export function downloadReportJson(report: SafetyReport, doc: Document = document): void {
  const blob = new Blob([reportToJson(report)], { type: "application/json" })
  const url = URL.createObjectURL(blob)
  const a = doc.createElement("a")
  a.href = url
  a.download = reportFileName(report.session_id)
  a.rel = "noopener"
  doc.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
