"use client"

import * as React from "react"

import { ApiRequestError } from "@/lib/api"
import type { WireTopic } from "@/lib/wire"
import {
  AUTO_SOURCES,
  HOLD_CONFIRM_TIMEOUT_MS,
  holdReducer,
  holdView,
  initialHoldState,
  intentForPress,
} from "@/lib/hold-machine"
import { useAuth } from "@/components/shell/auth-provider"
import { useRealtime } from "@/components/streamer/use-realtime"

export const HOLD_TOPICS: readonly WireTopic[] = ["control.hold", "engine.status"]

/**
 * Hold state for a screen that only needs the hold button (the /controls screen). It owns one realtime
 * connection, follows the `control.hold` echo and the engine's automatic sources, and sends presses over REST.
 * /live keeps its own copy of this logic next to its other frame handlers.
 */
export function useHold() {
  const { api, status } = useAuth()
  const { client, state: connection } = useRealtime(HOLD_TOPICS, status === "ready")
  const [hold, dispatch] = React.useReducer(holdReducer, initialHoldState)
  const [confirmOpen, setConfirmOpen] = React.useState(false)
  const [engineHold, setEngineHold] = React.useState<boolean | null>(null)

  React.useEffect(() => {
    if (!client) return
    return client.onFrame((frame) => {
      if (frame.topic === "control.hold") dispatch({ type: "echo", command: frame.data.d })
      else if (frame.topic === "engine.status") {
        dispatch({ type: "auto", sources: frame.data.d.hold_sources.filter((s) => AUTO_SOURCES.includes(s)) })
        setEngineHold(frame.data.d.hold_active)
      }
    })
  }, [client])

  React.useEffect(() => {
    dispatch({ type: "connection", open: connection === "open" })
  }, [connection])

  React.useEffect(() => {
    if (status !== "ready") return
    api
      .getHold()
      .then((now) => dispatch({ type: "snapshot", sources: now.sources }))
      .catch(() => {})
  }, [api, status, connection === "open"]) // eslint-disable-line react-hooks/exhaustive-deps

  const send = React.useCallback(
    (active: boolean) => {
      dispatch({ type: "request", active })
      api
        .hold(active, { source: "app", releaseAll: !active })
        .then((response) => dispatch({ type: "rest-ok", response }))
        .catch((err: unknown) => {
          const e = err instanceof ApiRequestError ? err : null
          dispatch({ type: "rest-fail", code: e?.isNetwork ? "network" : e?.isUnauthorized ? "unauthorized" : "rejected" })
        })
    },
    [api],
  )

  React.useEffect(() => {
    if (hold.requestSeq === 0) return
    const id = setTimeout(() => dispatch({ type: "timeout" }), HOLD_CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(id)
  }, [hold.requestSeq])

  const view = holdView(hold)
  const press = React.useCallback(() => {
    switch (intentForPress(view)) {
      case "hold":
      case "retry-hold":
        send(true)
        break
      case "retry-resume":
        send(false)
        break
      case "confirm-resume":
        setConfirmOpen(true)
        break
      case null:
        break
    }
  }, [view, send])

  return {
    view,
    press,
    confirmOpen,
    setConfirmOpen,
    confirmResume: () => {
      setConfirmOpen(false)
      send(false)
    },
    engineConfirmed: engineHold !== null && engineHold === view.held,
  }
}
