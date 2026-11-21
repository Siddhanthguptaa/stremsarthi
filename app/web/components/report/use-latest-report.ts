"use client"

import * as React from "react"
import { usePathname } from "next/navigation"

import { createReportClient, latestSessionId } from "@/lib/report-api"
import { useAuth } from "@/components/shell/auth-provider"

/**
 * The newest session that has a report (`GET /api/report`), for the "Safety report" nav link. Null without a token,
 * for roles that may not read reports (moderator, hotkey: 403), when there is no session yet, or when the API is
 * down. Looked up again on every page change, so the link appears once a stream has run.
 */
export function useLatestReportId(): string | null {
  const { status, getToken } = useAuth()
  const pathname = usePathname()
  const [id, setId] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (status !== "ready") return
    const controller = new AbortController()
    createReportClient({ getToken, timeoutMs: 5000 })
      .listSessions(controller.signal)
      .then((ids) => setId(latestSessionId(ids)))
      .catch(() => {
        if (!controller.signal.aborted) setId(null)
      })
    return () => controller.abort()
  }, [status, getToken, pathname])

  return status === "ready" ? id : null
}
