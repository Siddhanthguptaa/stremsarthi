"use client"

import * as React from "react"
import { AlertTriangle, CheckCircle2 } from "lucide-react"

import type { ApiClient, EnrolList } from "@/lib/api"
import type { CaptureDeps } from "@/lib/capture"
import { cleanLabel, enrolErrorKey, LABEL_MAX } from "@/lib/enrol"
import type { I18nKey } from "@/lib/i18n"
import { CameraCapture } from "@/components/enrollment/camera-capture"
import { FaceCard } from "@/components/enrollment/face-card"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { useLanguage } from "@/components/shell/language-provider"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/** The /enrol screen: needs a token, then shows the panel. */
export function EnrolScreen() {
  return (
    <RequireToken>
      <EnrolWithAuth />
    </RequireToken>
  )
}

function EnrolWithAuth() {
  const { api } = useAuth()
  return <EnrolPanel api={api} />
}

type EnrolApi = Pick<ApiClient, "getEnrolled" | "enrol" | "deleteEnrolled">

type Message = { kind: "ok" | "error"; key: I18nKey } | null

function useNow(intervalMs: number): number {
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}

interface EnrolPanelProps {
  api: EnrolApi
  /** Test seam for the camera. */
  captureDeps?: Partial<CaptureDeps>
}

/**
 * Face allowlist: a name, a short camera burst, and the list of enrolled people with expiry and remove.
 * The browser keeps no photos and no face data: frames go to the API once, the list shows labels and times.
 */
export function EnrolPanel({ api, captureDeps }: EnrolPanelProps) {
  const { t } = useLanguage()
  const [list, setList] = React.useState<EnrolList | null>(null)
  const [loadFailed, setLoadFailed] = React.useState(false)
  const [label, setLabel] = React.useState("")
  const [message, setMessage] = React.useState<Message>(null)
  const [removing, setRemoving] = React.useState<ReadonlySet<string>>(new Set())
  const now = useNow(30_000)
  const labelId = React.useId()

  const refresh = React.useCallback(
    async (signal?: AbortSignal) => {
      try {
        setList(await api.getEnrolled(signal))
        setLoadFailed(false)
      } catch {
        if (!signal?.aborted) setLoadFailed(true)
      }
    },
    [api],
  )

  React.useEffect(() => {
    const controller = new AbortController()
    api
      .getEnrolled(controller.signal)
      .then((next) => {
        setList(next)
        setLoadFailed(false)
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadFailed(true)
      })
    return () => controller.abort()
  }, [api])

  const faces = list?.faces ?? []
  const full = list !== null && faces.length >= list.max_faces
  const cleaned = cleanLabel(label)
  const canScan = list !== null && !full && cleaned.length > 0
  const blockedHint = full ? t("enrol.full") : cleaned.length === 0 ? t("enrol.label.need") : undefined

  const onCaptured = React.useCallback(
    async (frames: Blob[]) => {
      setMessage(null)
      try {
        await api.enrol(cleaned, frames)
        setLabel("")
        setMessage({ kind: "ok", key: "enrol.added" })
        await refresh()
      } catch (error) {
        setMessage({ kind: "error", key: enrolErrorKey(error) })
      }
    },
    [api, cleaned, refresh],
  )

  const onRemove = React.useCallback(
    async (face: { face_id: string }) => {
      setRemoving((s) => new Set(s).add(face.face_id))
      try {
        await api.deleteEnrolled(face.face_id)
        setList((l) => (l ? { ...l, faces: l.faces.filter((f) => f.face_id !== face.face_id) } : l))
        setMessage({ kind: "ok", key: "enrol.removed" })
      } catch {
        setMessage({ kind: "error", key: "enrol.removeFailed" })
      } finally {
        setRemoving((s) => {
          const next = new Set(s)
          next.delete(face.face_id)
          return next
        })
      }
    },
    [api],
  )

  return (
    <section className="space-y-6" aria-labelledby="enrol-title">
      <header className="space-y-1">
        <h1 id="enrol-title" className="text-2xl font-bold tracking-tight">
          {t("page.enrol.title")}
        </h1>
        <p className="text-muted-foreground">{t("page.enrol.desc")}</p>
      </header>

      <div className="space-y-1.5">
        <Label htmlFor={labelId}>{t("enrol.label.field")}</Label>
        <Input
          id={labelId}
          value={label}
          maxLength={LABEL_MAX}
          autoComplete="off"
          autoCapitalize="words"
          spellCheck={false}
          className="h-11"
          aria-describedby={`${labelId}-hint`}
          onChange={(e) => setLabel(e.target.value)}
        />
        <p id={`${labelId}-hint`} className="text-xs text-muted-foreground">
          {t("enrol.label.hint")}
        </p>
      </div>

      <CameraCapture onCaptured={onCaptured} canScan={canScan} blockedHint={blockedHint} deps={captureDeps} />

      <div role="status" aria-live="polite" className="min-h-6 text-sm" data-testid="enrol-message">
        {message?.kind === "ok" ? (
          <span className="inline-flex items-center gap-1.5 text-success">
            <CheckCircle2 aria-hidden className="size-4" />
            {t(message.key)}
          </span>
        ) : null}
        {message?.kind === "error" ? (
          <span role="alert" className="inline-flex items-center gap-1.5 text-destructive">
            <AlertTriangle aria-hidden className="size-4" />
            {t(message.key)}
          </span>
        ) : null}
      </div>

      <section aria-labelledby="enrolled-title" className="space-y-3">
        <h2 id="enrolled-title" className="flex flex-wrap items-baseline gap-2 text-lg font-semibold">
          {t("enrol.list.title")}
          {list ? (
            <span className="text-sm font-normal text-muted-foreground">
              {faces.length} {t("enrol.of")} {list.max_faces}
            </span>
          ) : null}
        </h2>
        {loadFailed ? <p role="alert">{t("enrol.list.loadFailed")}</p> : null}
        {list && faces.length === 0 ? <p className="text-muted-foreground">{t("enrol.list.empty")}</p> : null}
        <ul className="space-y-2">
          {faces.map((face) => (
            <li key={face.face_id}>
              <FaceCard face={face} nowMs={now} removing={removing.has(face.face_id)} onRemove={onRemove} />
            </li>
          ))}
        </ul>
      </section>
    </section>
  )
}
