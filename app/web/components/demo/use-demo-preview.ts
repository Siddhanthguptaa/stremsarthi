"use client"

import * as React from "react"

import { type DemoApi, type DemoStatus, createDemoApi } from "@/lib/demo-api"
import { STATUS_POLL_MS, retryDelayMs, tokenRefreshDelayMs } from "@/lib/demo-stream"
import { useAuth } from "@/components/shell/auth-provider"

/** The demo client, sharing the app's bearer token and its sign-out on 401. */
export function useDemoApi(): DemoApi {
  const { getToken, signOut } = useAuth()
  return React.useMemo(() => createDemoApi({ getToken, onUnauthorized: signOut }), [getToken, signOut])
}

export interface DemoStatusState {
  status: DemoStatus | null
  /** The last status call failed (the previous good status, if any, is kept). */
  failed: boolean
}

/** Polls `GET /api/demo/status` while mounted. */
export function useDemoStatus(api: DemoApi, enabled: boolean): DemoStatusState {
  const [state, setState] = React.useState<DemoStatusState>({ status: null, failed: false })

  React.useEffect(() => {
    if (!enabled) return
    let alive = true
    const controller = new AbortController()
    const poll = async () => {
      try {
        const status = await api.getStatus(controller.signal)
        if (alive) setState({ status, failed: false })
      } catch {
        if (alive) setState((prev) => (prev.failed ? prev : { ...prev, failed: true }))
      }
    }
    void poll()
    const id = setInterval(() => void poll(), STATUS_POLL_MS)
    return () => {
      alive = false
      controller.abort()
      clearInterval(id)
    }
  }, [api, enabled])

  return state
}

export interface PreviewTokenState {
  /** Only ever used to build an image URL. Never render or log it. */
  token: string | null
  failed: boolean
}

/**
 * Fetches a preview token and replaces it every 4 minutes (sooner if the API says it expires sooner).
 * A failed request is retried with back-off; the old token stays in use until a new one arrives.
 */
export function usePreviewToken(api: DemoApi, enabled: boolean): PreviewTokenState {
  const [state, setState] = React.useState<PreviewTokenState>({ token: null, failed: false })

  React.useEffect(() => {
    if (!enabled) return
    let alive = true
    let attempt = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    const run = async () => {
      try {
        const reply = await api.getPreviewToken(controller.signal)
        if (!alive) return
        attempt = 0
        setState({ token: reply.token, failed: false })
        timer = setTimeout(() => void run(), tokenRefreshDelayMs(reply.expires_in_s))
      } catch {
        if (!alive) return
        setState((prev) => (prev.failed ? prev : { ...prev, failed: true }))
        timer = setTimeout(() => void run(), retryDelayMs(attempt))
        attempt += 1
      }
    }
    void run()
    return () => {
      alive = false
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [api, enabled])

  return state
}
