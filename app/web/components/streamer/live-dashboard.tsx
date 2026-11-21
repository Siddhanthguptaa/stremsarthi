"use client"

import * as React from "react"
import { ApiRequestError } from "@/lib/api"
import {
  AUTO_SOURCES,
  HOLD_CONFIRM_TIMEOUT_MS,
  holdReducer,
  holdView,
  initialHoldState,
  intentForPress,
  shouldHandleHoldKey,
} from "@/lib/hold-machine"
import { chatOverlayReducer, initialChatOverlay } from "@/lib/chat-state"
import { captionReducer, initialCaptions } from "@/lib/captions"
import { initialRisk, riskReducer, riskView } from "@/lib/risk"
import { chipFor, engineIsFresh, initialLiveState, liveReducer } from "@/lib/live-state"
import type { WireTopic } from "@/lib/wire"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { useLanguage } from "@/components/shell/language-provider"
import { ConnectionBanner } from "@/components/shell/connection-banner"
import { AlertFeed } from "@/components/streamer/alert-feed"
import { ChatOverlay } from "@/components/streamer/chat-overlay"
import { CaptionsControl } from "@/components/streamer/captions-control"
import { EngineChip } from "@/components/streamer/engine-chip"
import { HoldButton } from "@/components/streamer/hold-button"
import { ModeratorInvite } from "@/components/streamer/moderator-invite"
import { RiskMeter } from "@/components/streamer/risk-meter"
import { useRealtime } from "@/components/streamer/use-realtime"
import { useStreamerApi } from "@/components/streamer/use-streamer-api"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"

const LIVE_TOPICS: readonly WireTopic[] = ["control.hold", "engine.status", "safety.event", "guard.risk", "guard.meter", "caption.event", "chat.allowed", "chat.verdict"]

/** The /live dashboard: HOLD button, status chip, alert feed, connection state. */
export function LiveDashboard() {
  return (
    <RequireToken>
      <Dashboard />
    </RequireToken>
  )
}

function useNow(intervalMs: number): number {
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}

function Dashboard() {
  const { api, status } = useAuth()
  const streamerApi = useStreamerApi()
  const { t } = useLanguage()
  const { client, state: connection } = useRealtime(LIVE_TOPICS, status === "ready")
  const [hold, dispatchHold] = React.useReducer(holdReducer, initialHoldState)
  const [live, dispatchLive] = React.useReducer(liveReducer, initialLiveState)
  const [chat, dispatchChat] = React.useReducer(chatOverlayReducer, initialChatOverlay)
  const [risk, dispatchRisk] = React.useReducer(riskReducer, initialRisk)
  const [captions, dispatchCaptions] = React.useReducer(captionReducer, initialCaptions)
  const [confirmOpen, setConfirmOpen] = React.useState(false)
  const [alertsFailed, setAlertsFailed] = React.useState(false)
  const now = useNow(1000)
  const riskNow = riskView(risk, now)

  // The /live page is dark by default to reduce glare (docs/specs/app-web.md 5.6).
  React.useEffect(() => {
    document.documentElement.classList.add("dark")
    return () => document.documentElement.classList.remove("dark")
  }, [])

  // --- realtime frames -> reducers ---------------------------------------------------------------
  React.useEffect(() => {
    if (!client) return
    return client.onFrame((frame) => {
      switch (frame.topic) {
        case "control.hold":
          dispatchHold({ type: "echo", command: frame.data.d })
          break
        case "engine.status": {
          const status = frame.data.d
          dispatchLive({ type: "engine", status, at: Date.now() })
          dispatchHold({ type: "auto", sources: status.hold_sources.filter((s) => AUTO_SOURCES.includes(s)) })
          break
        }
        case "safety.event":
          dispatchLive({ type: "safety", event: frame.data.d })
          break
        case "guard.meter":
          dispatchRisk({ type: "meter", meter: frame.data.d, at: Date.now() })
          break
        case "guard.risk":
          dispatchRisk({ type: "signal", signal: frame.data.d, at: Date.now() })
          break
        case "caption.event":
          dispatchCaptions({ type: "caption", event: frame.data.d })
          break
        case "chat.allowed":
          dispatchChat({ type: "allowed", message: frame.data.d })
          break
        case "chat.verdict":
          if (frame.meta) dispatchChat({ type: "hidden-count", count: frame.meta.hidden_count })
          break
      }
    })
  }, [client])

  // A (re)connect replays the recent allowed messages and the hidden count: start the overlay again first.
  React.useEffect(() => {
    if (!client) return
    return client.onReplay((marker) => {
      if (marker.topic === "chat.allowed") dispatchChat({ type: "reset", hiddenCount: marker.hiddenCount })
      else if (marker.topic === "chat.verdict") dispatchChat({ type: "hidden-count", count: marker.hiddenCount })
    })
  }, [client])

  React.useEffect(() => {
    dispatchHold({ type: "connection", open: connection === "open" })
  }, [connection])

  // --- initial and post-reconnect state over REST ------------------------------------------------
  const refresh = React.useCallback(async () => {
    try {
      const [holdNow, statusNow] = await Promise.all([api.getHold(), api.getStatus()])
      dispatchLive({ type: "api", down: false })
      dispatchHold({ type: "snapshot", sources: holdNow.sources })
      // The feed's earlier alerts (first load, and what was missed while reconnecting). A failure here only
      // means the history is missing: live alerts keep arriving over the WebSocket.
      api
        .getAlerts({ limit: 50 })
        .then((r) => {
          setAlertsFailed(false)
          dispatchLive({ type: "backfill", alerts: r.alerts })
        })
        .catch(() => setAlertsFailed(true))
      // The risk meter right now (a missing endpoint only means the meter starts from the live frames).
      streamerApi
        .getRisk()
        .then((snapshot) => dispatchRisk({ type: "snapshot", snapshot, at: Date.now() }))
        .catch(() => {})
      if (statusNow.engine) dispatchLive({ type: "engine", status: statusNow.engine.d, at: Date.now() - (statusNow.engine_age_s ?? 0) * 1000 })
    } catch (err) {
      if (err instanceof ApiRequestError && err.isNetwork) dispatchLive({ type: "api", down: true })
    }
  }, [api, streamerApi])

  const wsOpen = connection === "open"
  React.useEffect(() => {
    if (status === "ready") void refresh() // on mount, and again after every (re)connect
  }, [status, refresh, wsOpen])

  // --- hold ---------------------------------------------------------------------------------------
  const view = holdView(hold)
  const engineFresh = engineIsFresh(live, now)

  const send = React.useCallback(
    (active: boolean) => {
      dispatchHold({ type: "request", active })
      api
        .hold(active, { source: "app", releaseAll: !active })
        .then((response) => {
          dispatchLive({ type: "api", down: false })
          dispatchHold({ type: "rest-ok", response })
        })
        .catch((err: unknown) => {
          const e = err instanceof ApiRequestError ? err : null
          if (e?.isNetwork) dispatchLive({ type: "api", down: true })
          dispatchHold({ type: "rest-fail", code: e?.isNetwork ? "network" : e?.isUnauthorized ? "unauthorized" : "rejected" })
        })
    },
    [api],
  )

  // The 2 s confirmation timer is keyed on the request, so a REST reply does not restart it.
  React.useEffect(() => {
    if (hold.requestSeq === 0) return
    const id = setTimeout(() => dispatchHold({ type: "timeout" }), HOLD_CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(id)
  }, [hold.requestSeq])

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

  // A failed or unconfirmed request is shown as a toast with a retry (the button also turns into Retry).
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

  // Global H key (not while typing, not with modifiers, not while the confirmation is open).
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

  const chip = chipFor(live, view.held, now, live.engineAt !== null)
  const engineConfirmed = engineFresh && live.engine !== null && live.engine.hold_active === view.held

  return (
    <div className="space-y-6 pb-52">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">{t("page.live.title")}</h1>
        <p className="text-muted-foreground">{t("page.live.desc")}</p>
      </header>

      <ConnectionBanner state={connection} apiDown={live.apiDown} />

      <EngineChip kind={chip} engine={engineFresh ? live.engine : null} />

      <RiskMeter view={riskNow} />

      <AlertFeed alerts={live.alerts} total={live.alertsTotal} loadFailed={alertsFailed} />

      <ChatOverlay state={chat} />

      <CaptionsControl api={api} lines={captions.lines} />

      <ModeratorInvite />

      <WakeLockSwitch />

      <div
        role="region"
        aria-label={t("hold.region")}
        className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 px-4 pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80"
        style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
      >
        <div className="mx-auto w-full max-w-5xl">
          <HoldButton
            view={view}
            engineConfirmed={engineConfirmed}
            onPress={press}
            confirmOpen={confirmOpen}
            onConfirmOpenChange={setConfirmOpen}
            onConfirmResume={() => {
              setConfirmOpen(false)
              send(false)
            }}
          />
        </div>
      </div>
    </div>
  )
}

/** Keeps the phone screen on while streaming (feature-detected; failure is ignored). */
function WakeLockSwitch() {
  const { t } = useLanguage()
  const supported = typeof navigator !== "undefined" && "wakeLock" in navigator
  const [enabled, setEnabled] = React.useState(true)

  React.useEffect(() => {
    if (!supported || !enabled) return
    let sentinel: WakeLockSentinel | null = null
    let cancelled = false
    const acquire = async () => {
      try {
        const next = await navigator.wakeLock.request("screen")
        if (cancelled) void next.release().catch(() => {})
        else sentinel = next
      } catch {
        /* denied or unsupported: the screen may dim, nothing else breaks */
      }
    }
    const onVisible = () => {
      if (document.visibilityState === "visible" && (!sentinel || sentinel.released)) void acquire()
    }
    void acquire()
    document.addEventListener("visibilitychange", onVisible)
    return () => {
      cancelled = true
      document.removeEventListener("visibilitychange", onVisible)
      void sentinel?.release().catch(() => {})
    }
  }, [supported, enabled])

  if (!supported) return <p className="text-xs text-muted-foreground">{t("live.keepAwakeNo")}</p>
  return (
    <div className="flex min-h-11 items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2">
      <Label htmlFor="keep-awake" className="text-base">
        {t("live.keepAwake")}
      </Label>
      <Switch id="keep-awake" checked={enabled} onCheckedChange={setEnabled} />
    </div>
  )
}
