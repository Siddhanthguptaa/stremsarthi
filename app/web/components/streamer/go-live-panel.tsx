"use client"

import * as React from "react"
import Link from "next/link"
import { AlertTriangle, CheckCircle2, CircleX, Loader2 } from "lucide-react"

import { ApiRequestError, type ApiClient, type PublicConfig } from "@/lib/api"
import type { DestinationKind, DestinationView, Pairing, SessionView, StartResponse, StreamerApi } from "@/lib/api-streamer"
import { glText, PLATFORM_NAME } from "@/lib/golive-text"
import {
  CHECK_LABELS,
  DELAY_MAX,
  DELAY_MIN,
  DELAY_STEP,
  DESTINATIONS,
  PROFILES,
  SESSION_STATE_KEY,
  buildStartRequest,
  clampDelay,
  formFromConfig,
  isActiveState,
  keyConfigured,
  readiness,
  sessionErrorKey,
  type SetupForm,
} from "@/lib/golive"
import type { I18nKey } from "@/lib/i18n"
import { setupText } from "@/lib/i18n-setup"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { useLanguage } from "@/components/shell/language-provider"
import { IngestCard } from "@/components/streamer/ingest-card"
import { SetupHeader, StatusLed } from "@/components/streamer/setup-header"
import { SignalPath } from "@/components/streamer/setup-signal-path"
import { useStreamerApi } from "@/components/streamer/use-streamer-api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"

export type SetupConfigApi = Pick<ApiClient, "getConfig">
export type SetupSessionApi = Pick<StreamerApi, "getSession" | "startSession" | "stopSession" | "getPairing" | "getPairingQr"> &
  Partial<Pick<StreamerApi, "listDestinations" | "setDestinationKey" | "clearDestinationKey">>

const POLL_MS = 3000

/** Route body for /go-live. */
export function GoLiveScreen() {
  return (
    <RequireToken>
      <GoLiveConnected />
    </RequireToken>
  )
}

function GoLiveConnected() {
  const { api } = useAuth()
  const streamerApi = useStreamerApi()
  return <GoLivePanel configApi={api} sessionApi={streamerApi} />
}

interface GoLivePanelProps {
  configApi: SetupConfigApi
  sessionApi: SetupSessionApi
  /** Polling interval for the session state while a stream is running (tests use a short one). */
  pollMs?: number
}

/** Go-live setup: destinations with key status, delay, profile, ingest addresses, setup check, start and stop. */
export function GoLivePanel({ configApi, sessionApi, pollMs = POLL_MS }: GoLivePanelProps) {
  const { t, lang } = useLanguage()
  const [config, setConfig] = React.useState<PublicConfig | null>(null)
  const [session, setSession] = React.useState<SessionView | null>(null)
  const [form, setForm] = React.useState<SetupForm | null>(null)
  const [loadFailed, setLoadFailed] = React.useState(false)
  const [busy, setBusy] = React.useState<"start" | "stop" | null>(null)
  const [errorKey, setErrorKey] = React.useState<I18nKey | null>(null)
  const [checks, setChecks] = React.useState<StartResponse["checks"] | null>(null)
  const [stopOpen, setStopOpen] = React.useState(false)
  const [pairing, setPairing] = React.useState<Pairing | null>(null)
  const [qrSrc, setQrSrc] = React.useState<string | null>(null)
  const [dests, setDests] = React.useState<DestinationView[]>([])
  const [keyDraft, setKeyDraft] = React.useState<Record<DestinationKind, string>>({ youtube: "", twitch: "" })
  const [keyMsg, setKeyMsg] = React.useState<{ kind: DestinationKind; text: string } | null>(null)
  const [newAddress, setNewAddress] = React.useState<{ stable: boolean } | null>(null)
  const delayId = React.useId()
  const profileId = React.useId()

  const state = session?.state ?? null
  const active = isActiveState(state)
  // A local rehearsal may be switched to a live session in one step; a live session never is.
  const rehearsalActive = active && session?.output === "rehearsal"
  const locked = active && !rehearsalActive
  const typed = React.useMemo(
    () => ({ youtube: dests.find((d) => d.kind === "youtube")?.configured === true, twitch: dests.find((d) => d.kind === "twitch")?.configured === true }),
    [dests],
  )
  const loadDests = React.useCallback(() => {
    sessionApi
      .listDestinations?.()
      .then((r) => setDests(r.destinations))
      .catch(() => {
        /* the .env status from the config still shows */
      })
  }, [sessionApi])
  React.useEffect(() => loadDests(), [loadDests])

  const load = React.useCallback(() => {
    Promise.all([configApi.getConfig(), sessionApi.getSession()])
      .then(([cfg, sess]) => {
        setLoadFailed(false)
        setConfig(cfg)
        setSession(sess)
        // The form starts from the running session's delay and profile, else from the saved config.
        const base = formFromConfig(cfg)
        setForm(isActiveState(sess.state) ? { ...base, delay: clampDelay(sess.delay_seconds), profile: sess.profile } : base)
      })
      .catch(() => setLoadFailed(true))
  }, [configApi, sessionApi])

  React.useEffect(() => load(), [load])

  // Follow the session while it is starting, running or stopping.
  React.useEffect(() => {
    if (!active) return
    const timer = setInterval(() => {
      sessionApi
        .getSession()
        .then(setSession)
        .catch(() => {
          /* keep the last known state; the next poll tries again */
        })
    }, pollMs)
    return () => clearInterval(timer)
  }, [active, sessionApi, pollMs])

  // Ingest details once a session exists. A missing endpoint (404) only means no QR code.
  React.useEffect(() => {
    if (!active) return
    let alive = true
    sessionApi
      .getPairing()
      .then((p) => alive && setPairing(p))
      .catch(() => alive && setPairing(null))
    return () => {
      alive = false
    }
  }, [active, sessionApi, session?.session_id])

  const shownPairing = active ? pairing : null
  React.useEffect(() => {
    if (!shownPairing) return
    let alive = true
    let url: string | null = null
    sessionApi
      .getPairingQr("rtmp")
      .then((blob) => {
        if (!alive) return
        url = URL.createObjectURL(blob)
        setQrSrc(url)
      })
      .catch(() => alive && setQrSrc(null))
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [shownPairing, sessionApi])

  const verdict = form ? readiness(form, config, typed) : null
  const names = (kinds: readonly string[]) => kinds.map((k) => t(`golive.dest.${k}` as I18nKey)).join(", ")

  const saveKey = async (kind: DestinationKind) => {
    const key = keyDraft[kind].trim()
    if (!key || !sessionApi.setDestinationKey) return
    try {
      await sessionApi.setDestinationKey(kind, key)
      setKeyDraft((d) => ({ ...d, [kind]: "" })) // the field is emptied at once: the key is never kept on screen
      setKeyMsg({ kind, text: glText(lang, "golive.key.saved") })
      loadDests()
    } catch (error) {
      const locked409 = error instanceof ApiRequestError && error.code === "session_active"
      setKeyMsg({ kind, text: glText(lang, locked409 ? "golive.key.locked" : "golive.key.bad") })
    }
  }

  const forgetKey = async (kind: DestinationKind) => {
    try {
      await sessionApi.clearDestinationKey?.(kind)
    } finally {
      setKeyMsg(null)
      loadDests()
    }
  }

  const start = async () => {
    if (!form || !verdict?.ok) return
    setBusy("start")
    setErrorKey(null)
    try {
      const response = await sessionApi.startSession(buildStartRequest(form, rehearsalActive))
      setChecks(response.checks)
      setNewAddress({ stable: response.login_stable === true })
      setPairing(null) // the login is new: never show the old address
      setSession(await sessionApi.getSession())
    } catch (error) {
      setErrorKey(sessionErrorKey(error))
    } finally {
      setBusy(null)
    }
  }

  const stop = async () => {
    setStopOpen(false)
    setBusy("stop")
    setErrorKey(null)
    try {
      await sessionApi.stopSession()
      setSession(await sessionApi.getSession())
    } catch (error) {
      setErrorKey(sessionErrorKey(error))
    } finally {
      setBusy(null)
    }
  }

  const stateLabel = state ? t(SESSION_STATE_KEY[state]) : ""
  const inFailure = state === "failed" || state === "engine_lost"
  const anyDest = form ? DESTINATIONS.some((k) => form.destinations[k]) : false
  const onAir = session?.output === "live" && active
  const stepTitle = (n: number, text: string) => (
    <h2 className="flex items-center gap-3 font-display text-xl font-bold">
      <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-desk-raised text-sm text-primary">
        {n}
      </span>
      {text}
    </h2>
  )

  return (
    <section className="space-y-6" aria-labelledby="golive-title">
      <SetupHeader id="golive-title" title={t("page.goLive.title")} description={t("page.goLive.desc")}>
        {form ? (
          <StatusLed tone={inFailure || onAir ? "tally" : verdict?.ok ? "success" : "warning"}>
            {stateLabel || t("golive.state.idle")}
          </StatusLed>
        ) : null}
      </SetupHeader>

      {!form && !loadFailed ? <p role="status">{t("golive.loading")}</p> : null}
      {loadFailed ? (
        <div role="alert" className="space-y-2">
          <p>{t("golive.loadFailed")}</p>
          <Button
            variant="outline"
            size="lg"
            onClick={() => {
              setLoadFailed(false)
              load()
            }}
          >
            {t("enrol.retry")}
          </Button>
        </div>
      ) : null}

      {form ? (
        <>
          <SignalPath state={state} destOk={anyDest && verdict?.ok === true} anyDest={anyDest} onAir={onAir} />

          <div className="grid items-start gap-4 lg:grid-cols-[1.4fr_1fr]">
            <div className="module space-y-6 p-4 md:p-5" role="group" aria-label={setupText(lang, "setup.checklist")}>
              <section className="space-y-3" aria-labelledby="step-dest">
                <div id="step-dest">{stepTitle(1, setupText(lang, "setup.step.1"))}</div>
                <p className="sr-only">
                  {t("golive.dest.title")}. {t("golive.dest.desc")}
                </p>
                <ul className="space-y-2">
                  {DESTINATIONS.map((kind) => {
                    const id = `dest-${kind}`
                    const configured = keyConfigured(config, kind, typed)
                    const view = dests.find((d) => d.kind === kind)
                    const fromEnv = config?.secrets?.[`${kind}_stream_key`] === "***"
                    const source = view?.key_source ?? (fromEnv ? "env" : null)
                    const name = PLATFORM_NAME[kind]
                    const runningLive = session?.output === "live" && (session.destinations ?? []).includes(kind) && active
                    return (
                      <li key={kind} className="module-row space-y-2 p-3">
                        <div className="flex min-h-11 items-center justify-between gap-3">
                          <div className="flex min-w-0 flex-wrap items-center gap-2">
                            <Label htmlFor={id} className="font-display text-xl font-bold">
                              {t(`golive.dest.${kind}` as I18nKey)}
                            </Label>
                            <Badge variant={configured ? "secondary" : "destructive"} className="gap-1">
                              {configured ? <CheckCircle2 aria-hidden className="size-3.5" /> : <CircleX aria-hidden className="size-3.5" />}
                              {configured ? t("golive.key.configured") : t("golive.key.missing")}
                            </Badge>
                          </div>
                          <Switch
                            id={id}
                            checked={form.destinations[kind]}
                            disabled={locked || busy !== null}
                            onCheckedChange={(on) => setForm({ ...form, destinations: { ...form.destinations, [kind]: on } })}
                          />
                        </div>
                        <p className="text-sm text-muted-foreground" data-testid={`dest-status-${kind}`}>
                          {source ? glText(lang, source === "env" ? "golive.keyFrom.env" : "golive.keyFrom.session") : glText(lang, "golive.keyNone")}
                          {" · "}
                          <span className={runningLive ? "font-semibold text-tally" : ""}>
                            {runningLive
                              ? glText(lang, "golive.out.liveNow")
                              : form.destinations[kind]
                                ? glText(lang, "golive.out.rtmps", { name })
                                : glText(lang, "golive.out.previewOnly")}
                          </span>
                        </p>
                        {sessionApi.setDestinationKey && source !== "env" ? (
                          <div className="flex flex-wrap items-center gap-2">
                            <Label htmlFor={`key-${kind}`} className="sr-only">
                              {glText(lang, "golive.key.label", { name })}
                            </Label>
                            <Input
                              id={`key-${kind}`}
                              type="password"
                              autoComplete="off"
                              spellCheck={false}
                              className="min-w-0 flex-1"
                              placeholder={glText(lang, "golive.key.label", { name })}
                              value={keyDraft[kind]}
                              disabled={locked || busy !== null}
                              onChange={(e) => setKeyDraft((d) => ({ ...d, [kind]: e.target.value }))}
                            />
                            <Button
                              type="button"
                              variant="outline"
                              size="lg"
                              disabled={locked || busy !== null || keyDraft[kind].trim() === ""}
                              onClick={() => void saveKey(kind)}
                            >
                              {glText(lang, "golive.key.set")}
                            </Button>
                            {source === "session" ? (
                              <Button type="button" variant="ghost" size="lg" disabled={locked || busy !== null} onClick={() => void forgetKey(kind)}>
                                {glText(lang, "golive.key.clear")}
                              </Button>
                            ) : null}
                          </div>
                        ) : null}
                        {keyMsg?.kind === kind ? (
                          <p role="status" className="text-sm">
                            {keyMsg.text}
                          </p>
                        ) : null}
                      </li>
                    )
                  })}
                </ul>
                {DESTINATIONS.some((k) => !keyConfigured(config, k)) ? (
                  <p className="text-sm text-muted-foreground">{sessionApi.setDestinationKey ? glText(lang, "golive.key.hint") : t("golive.key.missingHint")}</p>
                ) : null}
              </section>

              <section className="space-y-3" aria-labelledby="step-delay">
                <div id="step-delay">{stepTitle(2, setupText(lang, "setup.step.2"))}</div>
                <div className="module-row space-y-4 p-3">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <Label htmlFor={delayId}>{t("golive.delay.label")}</Label>
                      <output htmlFor={delayId} className="timecode text-3xl font-bold">
                        {t("golive.delay.value").replace("{seconds}", form.delay.toFixed(1))}
                      </output>
                    </div>
                    <Slider
                      id={delayId}
                      thumbLabel={t("golive.delay.label")}
                      valueText={t("golive.delay.value").replace("{seconds}", form.delay.toFixed(1))}
                      min={DELAY_MIN}
                      max={DELAY_MAX}
                      step={DELAY_STEP}
                      value={[form.delay]}
                      disabled={active || busy !== null}
                      onValueChange={([value]) => setForm({ ...form, delay: clampDelay(value) })}
                    />
                    <p className="text-xs text-muted-foreground">{t("golive.delay.desc")}</p>
                    {active ? <p className="text-sm text-muted-foreground">{t("golive.delay.locked")}</p> : null}
                    {form.delay > 4.5 ? <p className="text-sm text-muted-foreground">{t("golive.delay.warn")}</p> : null}
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={profileId}>{t("golive.profile.label")}</Label>
                    <Select
                      id={profileId}
                      value={form.profile}
                      disabled={active || busy !== null}
                      onChange={(event) => setForm({ ...form, profile: event.target.value === "demo" ? "demo" : "dev" })}
                    >
                      {PROFILES.map((profile) => (
                        <option key={profile} value={profile}>
                          {t(`golive.profile.${profile}` as I18nKey)}
                        </option>
                      ))}
                    </Select>
                  </div>
                </div>
              </section>

              <section className="space-y-3" aria-labelledby="step-go">
                <div id="step-go">{stepTitle(3, setupText(lang, "setup.step.3"))}</div>
                <div className="module-row space-y-2 p-3">
                  <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                    <span className="font-semibold">{t("golive.session")}:</span>
                    <span role="status" aria-live="polite" data-testid="session-state" data-state={state ?? ""}>
                      {stateLabel}
                    </span>
                    {session?.session_id ? (
                      <span className="text-xs text-muted-foreground">
                        {t("golive.session.id")}: <code>{session.session_id}</code>
                      </span>
                    ) : null}
                  </p>
                  {session?.output ? (
                    <p className="text-sm font-semibold" data-testid="output-mode" data-mode={session.output}>
                      {session.output === "live" ? glText(lang, "golive.out.liveNow") : glText(lang, "golive.out.rehearsalNow")}
                    </p>
                  ) : null}
                  {rehearsalActive ? <p className="text-sm text-muted-foreground">{glText(lang, "golive.rehearsalRunning")}</p> : null}
                  {newAddress && active ? (
                    <p role="status" className="rounded-md border border-warning bg-warning/10 px-3 py-2 text-sm" data-testid="new-address">
                      {glText(lang, newAddress.stable ? "golive.stableLogin" : "golive.newAddress")}
                    </p>
                  ) : null}
                  {verdict && !locked ? (
                    <p id="golive-readiness" className="text-sm" data-testid="readiness">
                      {verdict.ok
                        ? t("golive.ready.ok")
                        : verdict.reason === "no_destination"
                          ? t("golive.ready.no_destination")
                          : t("golive.ready.missing_keys").replace("{names}", names(verdict.missing))}
                    </p>
                  ) : null}
                  {checks ? (
                    <div className="space-y-1 pt-1">
                      <p className="text-sm font-semibold">{t("golive.checks.title")}</p>
                      <p className="text-xs text-muted-foreground">{t("golive.checks.desc")}</p>
                      <ul className="space-y-1.5 pt-1" data-testid="setup-checks">
                        {CHECK_LABELS.map(([key, label]) => (
                          <li key={key} className="flex items-center justify-between gap-3 text-sm">
                            <span className="flex items-center gap-2">
                              <span aria-hidden className={`led ${checks[key] ? "text-success" : "text-muted-foreground"}`} />
                              {t(label)}
                            </span>
                            <span className={checks[key] ? "inline-flex items-center gap-1 font-medium text-success" : "text-muted-foreground"}>
                              {checks[key] ? <CheckCircle2 aria-hidden className="size-4" /> : null}
                              {checks[key] ? t("golive.check.ok") : t("golive.check.off")}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>

                <div role="alert" className="min-h-6">
                  {errorKey ? (
                    <p className="flex items-start gap-2 text-sm font-medium text-destructive">
                      <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
                      {t(errorKey)}
                    </p>
                  ) : null}
                </div>

                {rehearsalActive ? (
                  <Button
                    type="button"
                    size="lg"
                    className="min-h-14 w-full bg-destructive text-lg text-destructive-foreground hover:bg-destructive/90"
                    disabled={!verdict?.ok || busy !== null || state === "stopping"}
                    aria-describedby="golive-readiness"
                    onClick={() => void start()}
                  >
                    {busy === "start" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}
                    {busy === "start" ? glText(lang, "golive.switching") : glText(lang, "golive.switch")}
                  </Button>
                ) : null}

                {active ? (
                  <Button
                    type="button"
                    variant="destructive"
                    size="lg"
                    className="min-h-14 w-full text-lg"
                    disabled={busy !== null || state === "stopping"}
                    onClick={() => setStopOpen(true)}
                  >
                    {busy === "stop" || state === "stopping" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}
                    {busy === "stop" || state === "stopping" ? t("golive.stopping") : t("golive.stop")}
                  </Button>
                ) : (
                  <Button
                    type="button"
                    size="lg"
                    className="min-h-14 w-full text-lg"
                    disabled={!verdict?.ok || busy !== null}
                    aria-describedby="golive-readiness"
                    onClick={() => void start()}
                  >
                    {busy === "start" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}
                    {busy === "start" ? t("golive.starting") : t("golive.start")}
                  </Button>
                )}

                {inFailure ? (
                  <p role="alert" className="text-sm font-medium text-destructive">
                    {stateLabel}
                  </p>
                ) : null}

                {active ? (
                  <Button asChild variant="outline" size="lg" className="w-full">
                    <Link href="/live">{t("golive.toLive")}</Link>
                  </Button>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("golive.next")}</p>
                )}
              </section>
            </div>

            <IngestCard rtmpUrl={session?.ingest.rtmp_url ?? null} srtUrl={session?.ingest.srt_url ?? null} pairing={shownPairing} qrSrc={shownPairing ? qrSrc : null} />
          </div>
        </>
      ) : null}

      <Dialog open={stopOpen} onOpenChange={setStopOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("golive.stop.title")}</DialogTitle>
            <DialogDescription>{t("golive.stop.body")}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" className="min-h-12" onClick={() => setStopOpen(false)}>
              {t("golive.stop.cancel")}
            </Button>
            <Button type="button" variant="destructive" className="min-h-12" onClick={() => void stop()}>
              {t("golive.stop.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
