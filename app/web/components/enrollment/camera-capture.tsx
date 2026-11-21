/*
 * Adapted from Saximn/privastream (Apache-2.0), src/web/frontend/components/enrollment/cameracapture.tsx.
 * See LICENSE.privastream. Kept: getUserMedia with a front-camera preview and a canvas capture. Changed:
 * a timed burst of frames (not manual photos), live guidance, a permission-denied state, JPEG blobs instead
 * of PNG data URLs, no thumbnails and no stored photos, and the camera is stopped and the canvas cleared
 * the moment the burst ends.
 */
"use client"

import * as React from "react"
import { AlertTriangle, Camera, CameraOff, Loader2, ScanFace, ShieldCheck } from "lucide-react"

import { type CaptureDeps, defaultCaptureDeps, stopStream } from "@/lib/capture"
import {
  CAPTURE_DURATION_MS,
  CAPTURE_FRAMES,
  cameraFailure,
  captureOffsets,
  type GuidanceKey,
  GUIDANCE_INTERVAL_MS,
  guidanceFor,
} from "@/lib/enrol"
import type { I18nKey } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"
import { setupText } from "@/lib/i18n-setup"
import { Button } from "@/components/ui/button"

export type CameraPhase =
  | { phase: "idle" }
  | { phase: "requesting" }
  | { phase: "ready" }
  | { phase: "denied" }
  | { phase: "unavailable" }
  | { phase: "capturing"; taken: number }
  | { phase: "uploading" }

const GUIDE_KEYS: Record<GuidanceKey, I18nKey> = {
  ok: "enrol.guide.ok",
  tooDark: "enrol.guide.tooDark",
  tooBright: "enrol.guide.tooBright",
  blurry: "enrol.guide.blurry",
  noFace: "enrol.guide.noFace",
  multiple: "enrol.guide.multiple",
  tooSmall: "enrol.guide.tooSmall",
}

interface CameraCaptureProps {
  /** Receives the captured JPEG frames once. They are dropped right after it settles. */
  onCaptured: (frames: Blob[]) => Promise<void>
  /** The Scan button is enabled only when true (a label was entered and the list has room). */
  canScan: boolean
  /** Shown next to a disabled Scan button. */
  blockedHint?: string
  frames?: number
  durationMs?: number
  /** Test seam: camera, canvas and timer. */
  deps?: Partial<CaptureDeps>
}

/** Viewfinder corner brackets: four L shapes on the corners of the frame. */
function Brackets({ live }: { live: boolean }) {
  const c = live ? "border-success" : "border-primary"
  const base = `pointer-events-none absolute size-7 border-4 ${c}`
  return (
    <>
      <span aria-hidden className={`${base} left-3 top-3 border-b-0 border-r-0`} />
      <span aria-hidden className={`${base} right-3 top-3 border-b-0 border-l-0`} />
      <span aria-hidden className={`${base} bottom-3 left-3 border-r-0 border-t-0`} />
      <span aria-hidden className={`${base} bottom-3 right-3 border-l-0 border-t-0`} />
    </>
  )
}

export function CameraCapture({
  onCaptured,
  canScan,
  blockedHint,
  frames = CAPTURE_FRAMES,
  durationMs = CAPTURE_DURATION_MS,
  deps,
}: CameraCaptureProps) {
  const { t, lang } = useLanguage()
  const [state, setState] = React.useState<CameraPhase>({ phase: "idle" })
  const [guidance, setGuidance] = React.useState<GuidanceKey | null>(null)
  const [failed, setFailed] = React.useState(false)
  const videoRef = React.useRef<HTMLVideoElement | null>(null)
  const streamRef = React.useRef<MediaStream | null>(null)
  const mounted = React.useRef(true)
  const resolved = React.useMemo<CaptureDeps>(() => ({ ...defaultCaptureDeps(), ...deps }), [deps])

  const releaseCamera = React.useCallback(() => {
    stopStream(streamRef.current)
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }, [])

  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      releaseCamera() // leaving the page always turns the camera off
    }
  }, [releaseCamera])

  const live = state.phase === "ready" || state.phase === "capturing" || state.phase === "uploading"

  // Attach the stream once the <video> exists.
  const attachVideo = React.useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el
    if (el && streamRef.current && el.srcObject !== streamRef.current) {
      el.srcObject = streamRef.current
      void el.play?.()?.catch(() => undefined)
    }
  }, [])

  // Live guidance twice a second while the preview runs and before the burst starts.
  React.useEffect(() => {
    if (state.phase !== "ready") return
    let busy = false
    const timer = setInterval(async () => {
      const video = videoRef.current
      if (busy || !video) return
      busy = true
      try {
        const { stats, faces } = await resolved.sample(video)
        if (mounted.current) setGuidance(guidanceFor(stats, faces))
      } catch {
        /* guidance is a hint only */
      } finally {
        busy = false
      }
    }, GUIDANCE_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [state.phase, resolved])

  const start = async () => {
    setFailed(false)
    setGuidance(null)
    setState({ phase: "requesting" })
    let stream: MediaStream
    try {
      stream = await resolved.getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      })
    } catch (error) {
      if (mounted.current) setState({ phase: cameraFailure(error) })
      return
    }
    if (!mounted.current) {
      stopStream(stream)
      return
    }
    streamRef.current = stream
    setState({ phase: "ready" })
  }

  const stop = () => {
    releaseCamera()
    setGuidance(null)
    setState({ phase: "idle" })
  }

  const scan = async () => {
    const video = videoRef.current
    if (!video || state.phase !== "ready") return
    const grabber = resolved.createGrabber()
    const blobs: Blob[] = []
    const offsets = captureOffsets(frames, durationMs)
    setFailed(false)
    setState({ phase: "capturing", taken: 0 })
    try {
      for (let i = 0; i < offsets.length; i++) {
        if (i > 0) await resolved.sleep(offsets[i] - offsets[i - 1])
        if (!mounted.current) {
          grabber.dispose()
          return
        }
        blobs.push(await grabber.grab(video))
        setState({ phase: "capturing", taken: i + 1 })
      }
    } catch {
      blobs.length = 0
      grabber.dispose()
      releaseCamera()
      if (mounted.current) {
        setFailed(true)
        setState({ phase: "idle" })
      }
      return
    }
    // The burst is over: nothing more is read from the camera, and the canvas is cleared.
    grabber.dispose()
    releaseCamera()
    if (!mounted.current) return
    setState({ phase: "uploading" })
    try {
      await onCaptured(blobs)
    } catch {
      /* the parent reports its own errors */
    } finally {
      blobs.length = 0
    }
    if (mounted.current) setState({ phase: "idle" })
  }

  return (
    <section className="module space-y-4 p-3 md:p-4" data-phase={state.phase} aria-label={setupText(lang, "setup.enrol.stage")}>
      <div className="relative">
        {!live ? <div aria-hidden className="aspect-video w-full rounded-md bg-background" /> : null}
        {!live ? <Brackets live={false} /> : null}
        {!live ? (
          <div className="absolute inset-0 flex items-center justify-center overflow-y-auto p-6">
        {state.phase === "idle" ? (
          <div className="space-y-3 text-center">
            <div className="mx-auto w-fit rounded-full bg-desk-raised p-4">
              <Camera aria-hidden className="size-7" />
            </div>
            <p className="mx-auto max-w-md text-sm text-muted-foreground md:text-base">{t("enrol.poses")}</p>
            {failed ? (
              <p role="alert" className="text-destructive">
                {t("enrol.err.generic")}
              </p>
            ) : null}
            <Button size="lg" className="min-h-11 w-full sm:w-auto" onClick={start}>
              <Camera aria-hidden />
              {t("enrol.start")}
            </Button>
          </div>
        ) : null}

        {state.phase === "requesting" ? (
          <p role="status" className="flex items-center justify-center gap-2 py-6">
            <Loader2 aria-hidden className="size-5 animate-spin motion-reduce:animate-none" />
            {t("enrol.requesting")}
          </p>
        ) : null}

        {state.phase === "denied" || state.phase === "unavailable" ? (
          <div role="alert" className="space-y-3 text-center">
            <div className="mx-auto w-fit rounded-full bg-destructive/10 p-4 text-destructive">
              <CameraOff aria-hidden className="size-7" />
            </div>
            <h2 className="text-lg font-semibold">
              {state.phase === "denied" ? t("enrol.denied.title") : t("enrol.unavailable")}
            </h2>
            {state.phase === "denied" ? <p className="text-muted-foreground">{t("enrol.denied.body")}</p> : null}
            <Button size="lg" className="min-h-11 w-full sm:w-auto" onClick={start}>
              {t("enrol.retry")}
            </Button>
          </div>
        ) : null}

          </div>
        ) : null}
      </div>

        {live ? (
          <div className="space-y-3">
            <div className="relative w-full overflow-hidden rounded-md bg-black">
              <Brackets live />
              <video
                ref={attachVideo}
                autoPlay
                playsInline
                muted
                aria-label={t("enrol.start")}
                className="aspect-video w-full -scale-x-100 object-cover"
              />
            </div>

            <div role="status" aria-live="polite" className="min-h-12 text-center text-sm">
              {state.phase === "ready" ? (
                guidance ? (
                  <span
                    className={
                      guidance === "ok"
                        ? "inline-flex items-center gap-1.5 text-success"
                        : "inline-flex items-center gap-1.5 text-destructive"
                    }
                    data-guidance={guidance}
                  >
                    {guidance === "ok" ? (
                      <ScanFace aria-hidden className="size-4" />
                    ) : (
                      <AlertTriangle aria-hidden className="size-4" />
                    )}
                    {t(GUIDE_KEYS[guidance])}
                  </span>
                ) : (
                  <span className="text-muted-foreground">{t("enrol.poses")}</span>
                )
              ) : null}
              {state.phase === "capturing" ? <span className="font-medium">{t("enrol.capturing")}</span> : null}
              {state.phase === "uploading" ? (
                <span className="inline-flex items-center gap-1.5">
                  <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
                  {t("enrol.uploading")}
                </span>
              ) : null}
            </div>

            {state.phase === "capturing" ? (
              <div
                role="progressbar"
                aria-label={t("enrol.capturing")}
                aria-valuemin={0}
                aria-valuemax={frames}
                aria-valuenow={state.taken}
                className="h-2 w-full overflow-hidden rounded-full bg-secondary"
              >
                <div className="h-full bg-success" style={{ width: `${(state.taken / frames) * 100}%` }} />
              </div>
            ) : null}

            <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
              <Button size="lg" className="min-h-11" onClick={scan} disabled={state.phase !== "ready" || !canScan}>
                <ScanFace aria-hidden />
                {t("enrol.scan")}
              </Button>
              <Button
                size="lg"
                variant="outline"
                className="min-h-11"
                onClick={stop}
                disabled={state.phase !== "ready"}
              >
                {t("enrol.stopCamera")}
              </Button>
            </div>
            {!canScan && blockedHint && state.phase === "ready" ? (
              <p className="text-center text-sm text-muted-foreground">{blockedHint}</p>
            ) : null}
          </div>
        ) : null}

        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0" />
          {t("enrol.privacy")}
        </p>
    </section>
  )
}
