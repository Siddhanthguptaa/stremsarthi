"use client"

import * as React from "react"

import { type ApiClient, ApiRequestError } from "@/lib/api"
import {
  HOLD_CONFIRM_TIMEOUT_MS,
  type HoldEvent,
  holdReducer,
  holdView,
  initialHoldState,
  intentForPress,
  shouldHandleHoldKey,
} from "@/lib/hold-machine"
import { useLanguage } from "@/components/shell/language-provider"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"

interface HoldControlOptions {
  api: Pick<ApiClient, "hold">
  /** The WebSocket is open: the hold state then follows the `control.hold` echo, otherwise the REST reply. */
  wsOpen: boolean
  /** The REST call reached (or did not reach) the API. */
  onApiReachable?: (reachable: boolean) => void
}

/**
 * The same hold behaviour as `/live` (docs/specs/app-web.md 5.2), for the demo console: one tap holds, resuming
 * asks first, the shown state follows the WebSocket echo, a failure or a 2 s silence turns into a retry toast,
 * and the global H key toggles it. It uses the same `hold-machine` reducer as the `/live` dashboard.
 */
export function useHoldControl({ api, wsOpen, onApiReachable }: HoldControlOptions) {
  const { t } = useLanguage()
  const [state, dispatch] = React.useReducer(holdReducer, initialHoldState)
  const [confirmOpen, setConfirmOpen] = React.useState(false)
  const view = holdView(state)

  React.useEffect(() => {
    dispatch({ type: "connection", open: wsOpen })
  }, [wsOpen])

  const send = React.useCallback(
    (active: boolean) => {
      dispatch({ type: "request", active })
      api
        .hold(active, { source: "app", releaseAll: !active })
        .then((response) => {
          onApiReachable?.(true)
          dispatch({ type: "rest-ok", response })
        })
        .catch((err: unknown) => {
          const e = err instanceof ApiRequestError ? err : null
          if (e?.isNetwork) onApiReachable?.(false)
          dispatch({ type: "rest-fail", code: e?.isNetwork ? "network" : e?.isUnauthorized ? "unauthorized" : "rejected" })
        })
    },
    [api, onApiReachable],
  )

  // The 2 s confirmation timer is keyed on the request, so a REST reply does not restart it.
  React.useEffect(() => {
    if (state.requestSeq === 0) return
    const id = setTimeout(() => dispatch({ type: "timeout" }), HOLD_CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(id)
  }, [state.requestSeq])

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

  const failure = view.error
  React.useEffect(() => {
    if (!failure) return
    const retry = () => send(failure.wanted)
    const shown = toast({
      variant: "destructive",
      duration: 15000,
      title: t("toast.holdFailed"),
      description: t("toast.holdFailedDesc"),
      action: (
        <ToastAction altText={t("hold.retry")} onClick={retry}>
          {t("hold.retry")}
        </ToastAction>
      ),
    })
    return () => shown.dismiss()
  }, [failure, send, t])

  const pressRef = React.useRef(press)
  React.useEffect(() => {
    pressRef.current = press
  }, [press])
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const dialogOpen = document.querySelector('[role="dialog"], [role="alertdialog"]') !== null
      if (!shouldHandleHoldKey(event, dialogOpen)) return
      event.preventDefault()
      pressRef.current()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const confirmResume = React.useCallback(() => {
    setConfirmOpen(false)
    send(false)
  }, [send])

  return {
    state,
    view,
    /** Feed `control.hold` echoes, REST snapshots and the engine's automatic sources in here. */
    dispatch: dispatch as React.Dispatch<HoldEvent>,
    press,
    confirmOpen,
    setConfirmOpen,
    confirmResume,
  }
}
