"use client"

import * as React from "react"
import { EyeOff, ImageOff, Loader2 } from "lucide-react"

import type { DemoStatus, PreviewKind } from "@/lib/demo-api"
import type { DemoKey } from "@/lib/demo-i18n"
import {
  CONNECT_WATCHDOG_MS,
  FULL_RETRY_MS,
  type PaneMode,
  retryDelayMs,
  streamAvailable,
  viewerCapReached,
} from "@/lib/demo-stream"
import { cn } from "@/lib/utils"
import { useDemoText } from "@/components/demo/use-demo-text"

interface PreviewPaneProps {
  kind: PreviewKind
  mode: PaneMode
  /** The image URL (holds the preview token). `null` until a token has arrived. */
  src: string | null
  /** The token request failed and no token is available yet. */
  tokenFailed?: boolean
  /**
   * `GET /api/demo/status`. An `<img>` cannot read an HTTP status or body, so after an error (and while stuck on
   * "Connecting...") the pane asks the status endpoint why: viewer cap reached, or stream gone.
   */
  getStatus?: () => Promise<DemoStatus>
  /** Presenter mode: bigger labels for the stage. */
  large?: boolean
  /** The protected monitor is the program output: `onair` gets the tally border and ON AIR label, `hold` the HOLD label. */
  tally?: "onair" | "hold" | "off"
  /** The running clock, shown in the corner of the monitor. */
  timecode?: string
  /** Drawn on the monitor itself, along the bottom (the caption lower-third). */
  overlay?: React.ReactNode
  className?: string
}

const LABEL: Record<PreviewKind, DemoKey> = { raw: "stage.raw", protected: "stage.protected" }
const BADGE: Record<PreviewKind, DemoKey> = { raw: "badge.raw", protected: "badge.protected" }
const ALT: Record<PreviewKind, DemoKey> = { raw: "pane.raw.alt", protected: "pane.protected.alt" }

/**
 * One side of the side-by-side stage: a big label, a badge and the MJPEG stream (or a placeholder that says why
 * there is no picture). The image is a stream, not a stored file: nothing here keeps or saves a frame.
 */
export function PreviewPane({ kind, mode, src, tokenFailed = false, getStatus, large = false, tally = "off", timecode, overlay, className }: PreviewPaneProps) {
  const { d, alt, other, desk } = useDemoText()
  const headingId = React.useId()
  const isRaw = kind === "raw"

  let body: React.ReactNode
  if (mode === "disabled") {
    body = <RawDisabled />
  } else if (mode === "stream" && src) {
    // A new token means a new URL: the new image loads beside the old one and replaces it on its first frame.
    body = <StreamImage kind={kind} src={src} getStatus={getStatus} />
  } else if (mode === "stream") {
    body = tokenFailed ? <Notice icon="off" text={d("pane.unavailable")} /> : <Notice icon="wait" text={d("pane.connecting")} />
  } else if (mode === "waiting") {
    body = <Notice icon="wait" text={d("pane.waiting")} />
  } else if (mode === "unavailable") {
    body = <Notice icon="off" text={d("pane.unavailable")} extra={d("pane.rawUnavailable")} />
  } else {
    body = <Notice icon="wait" text={d("pane.connecting")} />
  }

  const onAir = !isRaw && tally === "onair"
  const holding = !isRaw && tally === "hold"
  const tallyLabel = onAir ? desk("tally.onair") : holding ? desk("tally.hold") : null

  return (
    <section aria-labelledby={headingId} data-pane={kind} data-mode={mode} data-tally={isRaw ? undefined : tally} className={cn("min-w-0 space-y-2", className)}>
      <div className="flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
        <h2 id={headingId} className={cn("font-display font-extrabold leading-none", isRaw ? "text-lg" : large ? "text-3xl lg:text-4xl" : "text-2xl lg:text-3xl")}>
          {d(LABEL[kind])}
          <span lang={other} className={cn("mt-1 block font-sans font-medium text-muted-foreground", large ? "text-base" : "text-sm")}>
            {alt(LABEL[kind])}
          </span>
        </h2>
        <p className="inline-flex min-h-7 items-center gap-2 rounded-md bg-background/60 px-2.5 text-xs font-semibold sm:text-sm">
          <span aria-hidden className={cn("led", isRaw ? "text-warning" : "text-success")} />
          {d(BADGE[kind])}
        </p>
      </div>
      <div
        className={cn(
          "relative w-full overflow-hidden rounded-md bg-black text-white",
          mode === "disabled" ? "min-h-40" : "aspect-video",
          onAir ? "border-2 border-tally" : holding ? "border-2 border-warning" : "border-2 border-border",
        )}
      >
        {body}
        {tallyLabel ? (
          <span
            aria-hidden
            data-testid="tally"
            className={cn(
              "absolute left-2 top-2 z-10 inline-flex items-center gap-2 rounded-sm px-2.5 py-1 font-display text-lg font-extrabold leading-none tracking-wider",
              onAir ? "bg-tally text-white" : "bg-warning text-warning-foreground",
            )}
          >
            <span className="size-2 rounded-full bg-current" />
            {tallyLabel}
          </span>
        ) : null}
        {timecode ? (
          <span aria-hidden suppressHydrationWarning className="timecode absolute right-2 top-2 z-10 rounded-sm bg-black/70 px-2 py-0.5 text-lg font-bold leading-tight text-white">
            {timecode}
          </span>
        ) : null}
        {overlay ? <div className="absolute inset-x-0 bottom-0 z-10">{overlay}</div> : null}
      </div>
    </section>
  )
}

function Notice({ icon, text, extra }: { icon: "wait" | "off"; text: string; extra?: string }) {
  return (
    <div role="status" className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center">
      {icon === "wait" ? (
        <Loader2 aria-hidden className="size-8 animate-spin motion-reduce:animate-none" />
      ) : (
        <ImageOff aria-hidden className="size-8" />
      )}
      <p className="text-base font-semibold sm:text-lg">{text}</p>
      {extra ? <p className="text-sm opacity-80">{extra}</p> : null}
    </div>
  )
}

/** The raw pane when the API has raw preview off: says why, and exactly how to turn it on. */
function RawDisabled() {
  const { d, alt, other } = useDemoText()
  return (
    <div role="status" data-testid="raw-disabled" className="flex min-h-40 flex-col items-center justify-center gap-2 p-4 text-center">
      <EyeOff aria-hidden className="size-8 shrink-0" />
      <p className="font-display text-xl font-bold">
        {d("raw.disabled.title")}
        <span lang={other} className="block text-sm font-semibold opacity-80">
          {alt("raw.disabled.title")}
        </span>
      </p>
      <p className="max-w-prose text-xs leading-snug text-white/80 sm:text-sm">{d("raw.disabled.body")}</p>
      <code className="rounded-sm bg-white/15 px-2 py-1 text-xs font-semibold sm:text-sm">{d("raw.disabled.env")}</code>
    </div>
  )
}

/** The MJPEG `<img>`. It reloads itself with a back-off after an error and never stays blank without saying why. */
function StreamImage({ kind, src, getStatus }: { kind: PreviewKind; src: string; getStatus?: () => Promise<DemoStatus> }) {
  const { d } = useDemoText()
  /** The URL on screen. A newer `src` (token refresh) loads beside it and replaces it on its first frame. */
  const [shown, setShown] = React.useState(src)
  const [loaded, setLoaded] = React.useState(false)
  const [failed, setFailed] = React.useState(false)
  /** The viewer cap (N max) when the last error was the API's "preview_clients_full"; otherwise null. */
  const [cap, setCap] = React.useState<number | null>(null)
  const [round, setRound] = React.useState(0)
  const attempt = React.useRef(0)
  const loadedRef = React.useRef(false)
  const statusRef = React.useRef(getStatus)
  React.useEffect(() => {
    statusRef.current = getStatus
  }, [getStatus])

  const pending = src !== shown ? src : null

  const onLoaded = React.useCallback(() => {
    attempt.current = 0
    loadedRef.current = true
    setLoaded(true)
    setFailed(false)
  }, [])
  const onFailed = React.useCallback(() => {
    loadedRef.current = false
    setLoaded(false)
    setCap(null)
    setFailed(true)
  }, [])

  // After an error: ask the status endpoint why (an <img> cannot read the 503 body), then retry. A full preview is
  // retried every 2 s; any other failure backs off 1, 2, 4, 8, 15 s.
  React.useEffect(() => {
    if (!failed) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    void (async () => {
      let cappedAt: number | null = null
      try {
        const status = await statusRef.current?.()
        if (status && viewerCapReached(kind, status)) cappedAt = status.max_clients
      } catch {
        /* the status call failed too: treat it as an ordinary outage */
      }
      if (!alive) return
      setCap(cappedAt)
      timer = setTimeout(
        () => {
          if (cappedAt === null) attempt.current += 1
          setFailed(false)
          setRound((r) => r + 1) // a new element makes a new request for the same URL
        },
        cappedAt !== null ? FULL_RETRY_MS : retryDelayMs(attempt.current),
      )
    })()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [failed, kind])

  // Watchdog: stuck on "Connecting..." for 12 s while the API says the stream exists -> mount the image again.
  React.useEffect(() => {
    if (loaded || failed) return
    const id = setInterval(() => {
      void (async () => {
        try {
          const status = await statusRef.current?.()
          if (status && !loadedRef.current && streamAvailable(kind, status)) setRound((r) => r + 1)
        } catch {
          /* no status: leave the image alone */
        }
      })()
    }, CONNECT_WATCHDOG_MS)
    return () => clearInterval(id)
  }, [loaded, failed, round, kind])

  // A replacement image that never shows a frame must not hold the swap forever: give up after 12 s and use it.
  React.useEffect(() => {
    if (pending === null) return
    const id = setTimeout(() => {
      loadedRef.current = false
      setLoaded(false)
      setShown(pending)
    }, CONNECT_WATCHDOG_MS)
    return () => clearTimeout(id)
  }, [pending])

  if (failed) {
    return cap !== null ? (
      <Notice icon="off" text={d("pane.full").replace("{n}", String(cap))} extra={d("pane.fullRetry")} />
    ) : (
      <Notice icon="off" text={d("pane.retrying")} />
    )
  }
  const label = d(ALT[kind])
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- a live multipart MJPEG stream, not an optimisable image */}
      <img
        key={`${shown}|${round}`}
        src={shown}
        alt={label}
        role="img"
        aria-label={label}
        draggable={false}
        decoding="async"
        referrerPolicy="no-referrer"
        data-slot="current"
        className="absolute inset-0 size-full select-none object-contain"
        onLoad={onLoaded}
        onError={() => {
          if (pending !== null) setShown(pending) // the old token's connection ended: retry with the new one
          onFailed()
        }}
      />
      {pending !== null ? (
        // eslint-disable-next-line @next/next/no-img-element -- see above
        <img
          key={`${pending}|${round}`}
          src={pending}
          alt=""
          aria-hidden
          draggable={false}
          decoding="async"
          referrerPolicy="no-referrer"
          data-slot="next"
          className="pointer-events-none absolute inset-0 size-full object-contain opacity-0"
          onLoad={() => {
            // First frame of the new connection: show it, and the old image unmounts (its connection closes).
            onLoaded()
            setShown(pending)
          }}
          onError={() => {
            setShown(pending)
            onFailed()
          }}
        />
      ) : null}
      {loaded ? null : <Notice icon="wait" text={d("pane.connecting")} />}
    </>
  )
}
