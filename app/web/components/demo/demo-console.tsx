"use client"

import * as React from "react"

import { ApiRequestError } from "@/lib/api"
import { captionReducer, initialCaptions } from "@/lib/captions"
import { initialProof, proofReducer } from "@/lib/demo-proof"
import { paneMode } from "@/lib/demo-stream"
import { AUTO_SOURCES } from "@/lib/hold-machine"
import { chipFor, engineIsFresh, initialLiveState, liveReducer } from "@/lib/live-state"
import { initialRisk, riskReducer, riskView } from "@/lib/risk"
import type { WireTopic } from "@/lib/wire"
import { CaptionStrip } from "@/components/demo/caption-strip"
import { PreviewPane } from "@/components/demo/preview-pane"
import { ProofCounter } from "@/components/demo/proof-counter"
import { StageStateChip } from "@/components/demo/stage-state"
import { useDemoApi, useDemoStatus, usePreviewToken } from "@/components/demo/use-demo-preview"
import { useDemoText } from "@/components/demo/use-demo-text"
import { useHoldControl } from "@/components/demo/use-hold-control"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { ConnectionBanner } from "@/components/shell/connection-banner"
import { AlertFeed } from "@/components/streamer/alert-feed"
import { CaptionsControl } from "@/components/streamer/captions-control"
import { HoldButton } from "@/components/streamer/hold-button"
import { RiskMeter } from "@/components/streamer/risk-meter"
import { useRealtime } from "@/components/streamer/use-realtime"
import { useStreamerApi } from "@/components/streamer/use-streamer-api"
import { useLanguage } from "@/components/shell/language-provider"

const DEMO_TOPICS: readonly WireTopic[] = ["control.hold", "engine.status", "safety.event", "guard.risk", "guard.meter", "caption.event"]

/** `/demo`: the judge-facing screen. RAW (streamer) beside PROTECTED (viewers), captions, alerts, risk, hold, proof. */
export function DemoConsole() {
  return (
    <RequireToken>
      <Console />
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

function Console() {
  const { api, status } = useAuth()
  const streamerApi = useStreamerApi()
  const demoApi = useDemoApi()
  const { t } = useLanguage()
  const { d } = useDemoText()
  const { client, state: connection } = useRealtime(DEMO_TOPICS, status === "ready")
  const [live, dispatchLive] = React.useReducer(liveReducer, initialLiveState)
  const [risk, dispatchRisk] = React.useReducer(riskReducer, initialRisk)
  const [captions, dispatchCaptions] = React.useReducer(captionReducer, initialCaptions)
  const [proof, dispatchProof] = React.useReducer(proofReducer, initialProof)
  const [alertsFailed, setAlertsFailed] = React.useState(false)
  const sessionRef = React.useRef<string | null>(null)
  const now = useNow(1000)

  const onApiReachable = React.useCallback((reachable: boolean) => dispatchLive({ type: "api", down: !reachable }), [])
  const hold = useHoldControl({ api, wsOpen: connection === "open", onApiReachable })
  const dispatchHold = hold.dispatch

  // High-contrast stage: dark like /live.
  React.useEffect(() => {
    document.documentElement.classList.add("dark")
    return () => document.documentElement.classList.remove("dark")
  }, [])

  // --- realtime frames -> reducers -----------------------------------------------------------------
  React.useEffect(() => {
    if (!client) return
    return client.onFrame((frame) => {
      switch (frame.topic) {
        case "control.hold":
          dispatchHold({ type: "echo", command: frame.data.d })
          break
        case "engine.status": {
          const engine = frame.data.d
          dispatchLive({ type: "engine", status: engine, at: Date.now() })
          dispatchHold({ type: "auto", sources: engine.hold_sources.filter((s) => AUTO_SOURCES.includes(s)) })
          break
        }
        case "safety.event":
          dispatchLive({ type: "safety", event: frame.data.d })
          dispatchProof({ type: "event", event: frame.data.d })
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
      }
    })
  }, [client, dispatchHold])

  // --- first load and after every (re)connect, over REST ---------------------------------------------
  const wsOpen = connection === "open"
  React.useEffect(() => {
    if (status !== "ready") return
    let alive = true
    const load = async () => {
      try {
        const [holdNow, statusNow] = await Promise.all([api.getHold(), api.getStatus()])
        if (!alive) return
        dispatchLive({ type: "api", down: false })
        dispatchHold({ type: "snapshot", sources: holdNow.sources })
        if (statusNow.engine) dispatchLive({ type: "engine", status: statusNow.engine.d, at: Date.now() - (statusNow.engine_age_s ?? 0) * 1000 })
      } catch (err) {
        if (alive && err instanceof ApiRequestError && err.isNetwork) dispatchLive({ type: "api", down: true })
        return
      }
      // The privacy-proof counter counts from the start of the current session.
      let since: number | null = null
      try {
        const session = await streamerApi.getSession()
        if (!alive) return
        if (sessionRef.current !== null && session.session_id !== sessionRef.current) dispatchProof({ type: "reset" })
        sessionRef.current = session.session_id
        since = session.started_at ? Date.parse(session.started_at) : null
        if (since !== null && Number.isNaN(since)) since = null
      } catch {
        /* the counter then counts the alerts the API still holds */
      }
      try {
        const { alerts } = await api.getAlerts({ limit: 200 })
        if (!alive) return
        setAlertsFailed(false)
        dispatchLive({ type: "backfill", alerts })
        dispatchProof({ type: "backfill", alerts, since })
      } catch {
        if (alive) setAlertsFailed(true)
      }
      streamerApi
        .getRisk()
        .then((snapshot) => alive && dispatchRisk({ type: "snapshot", snapshot, at: Date.now() }))
        .catch(() => {})
    }
    void load()
    return () => {
      alive = false
    }
  }, [status, api, streamerApi, dispatchHold, wsOpen])

  // --- the two previews --------------------------------------------------------------------------------
  const demoStatus = useDemoStatus(demoApi, status === "ready")
  const rawMode = paneMode("raw", demoStatus.status, demoStatus.failed)
  const protectedMode = paneMode("protected", demoStatus.status, demoStatus.failed)
  const preview = usePreviewToken(demoApi, status === "ready" && (rawMode === "stream" || protectedMode === "stream"))
  const getDemoStatus = React.useCallback(() => demoApi.getStatus(), [demoApi])
  const urlFor = (kind: "raw" | "protected") => (preview.token ? demoApi.url(kind, preview.token) : null)

  const engineFresh = engineIsFresh(live, now)
  const chip = chipFor(live, hold.view.held, now, live.engineAt !== null)
  const engineConfirmed = engineFresh && live.engine !== null && live.engine.hold_active === hold.view.held
  const riskNow = riskView(risk, now)

  return (
    <div className="space-y-6 pb-52" data-testid="demo-console">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">{d("demo.title")}</h1>
          <p className="text-muted-foreground">{d("demo.desc")}</p>
          {demoStatus.status ? (
            <p className="text-xs text-muted-foreground" data-testid="preview-clients">
              {d("preview.clients")}: {demoStatus.status.clients}
            </p>
          ) : null}
        </div>
        <StageStateChip kind={chip} />
      </header>

      <ConnectionBanner state={connection} apiDown={live.apiDown} />

      <section aria-label={d("demo.stage")} className="grid gap-5 md:grid-cols-2">
        <PreviewPane kind="raw" mode={rawMode} src={urlFor("raw")} tokenFailed={preview.failed} getStatus={getDemoStatus} />
        <PreviewPane kind="protected" mode={protectedMode} src={urlFor("protected")} tokenFailed={preview.failed} getStatus={getDemoStatus} />
      </section>

      <CaptionStrip lines={captions.lines} />

      <div className="grid gap-5 lg:grid-cols-2">
        <RiskMeter view={riskNow} />
        <ProofCounter state={proof} />
      </div>

      <AlertFeed alerts={live.alerts} total={live.alertsTotal} loadFailed={alertsFailed} />

      <details className="rounded-lg border bg-card p-3">
        <summary className="min-h-11 cursor-pointer py-2 text-base font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {d("settings.title")}
        </summary>
        <div className="pt-3">
          <CaptionsControl api={api} lines={captions.lines} />
        </div>
      </details>

      <div
        role="region"
        aria-label={t("hold.region")}
        className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 px-4 pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80"
        style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
      >
        <div className="mx-auto w-full max-w-5xl">
          <HoldButton
            view={hold.view}
            engineConfirmed={engineConfirmed}
            onPress={hold.press}
            confirmOpen={hold.confirmOpen}
            onConfirmOpenChange={hold.setConfirmOpen}
            onConfirmResume={hold.confirmResume}
          />
        </div>
      </div>
    </div>
  )
}
