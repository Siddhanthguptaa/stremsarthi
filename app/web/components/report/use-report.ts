"use client"

import * as React from "react"

import { createReportClient, ReportApiError } from "@/lib/report-api"
import type { SafetyReport } from "@/lib/report-types"
import { useAuth } from "@/components/shell/auth-provider"

export type ReportState =
  | { status: "loading" }
  | { status: "ready"; report: SafetyReport }
  | { status: "error"; code: "network" | "not_found" | "forbidden" | "bad_report" | "generic" }

/** A report of a stream that is still running is refreshed this often. */
export const PARTIAL_REFRESH_MS = 15_000

function codeOf(err: unknown): Extract<ReportState, { status: "error" }>["code"] {
  if (!(err instanceof ReportApiError)) return "generic"
  if (err.status === 0) return "network"
  if (err.status === 404) return "not_found"
  if (err.status === 403) return "forbidden"
  if (err.code === "bad_report") return "bad_report"
  return "generic"
}

/** Load `GET /api/report/{id}`; reload on demand; refresh while the stream is still running. */
export function useReport(sessionId: string) {
  const { getToken, signOut } = useAuth()
  const client = React.useMemo(() => createReportClient({ getToken, onUnauthorized: signOut }), [getToken, signOut])
  const [state, setState] = React.useState<ReportState>({ status: "loading" })
  const [tick, setTick] = React.useState(0)

  React.useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    client
      .getReport(sessionId, controller.signal)
      .then((report) => {
        setState({ status: "ready", report })
        if (report.ended_at === null) timer = setTimeout(() => setTick((n) => n + 1), PARTIAL_REFRESH_MS)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        // A failed refresh of a report we already show keeps showing it.
        setState((prev) => (prev.status === "ready" ? prev : { status: "error", code: codeOf(err) }))
      })
    return () => {
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [client, sessionId, tick])

  const reload = React.useCallback(() => {
    setState({ status: "loading" })
    setTick((n) => n + 1)
  }, [])

  return { state, reload }
}
