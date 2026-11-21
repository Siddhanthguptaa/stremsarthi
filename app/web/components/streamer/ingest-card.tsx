"use client"

import * as React from "react"
import { Check, Copy, Eye, EyeOff } from "lucide-react"

import type { Pairing } from "@/lib/api-streamer"
import { useLanguage } from "@/components/shell/language-provider"
import { Button } from "@/components/ui/button"

interface IngestCardProps {
  /** Per-session addresses from `GET /api/session` (null before the stream starts). */
  rtmpUrl: string | null
  srtUrl: string | null
  /** From `GET /api/pairing`; adds the per-session ingest key and the Moblin link. */
  pairing?: Pairing | null
  /** Object URL of the Moblin QR PNG (the QR carries the ingest address only). */
  qrSrc?: string | null
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const { t } = useLanguage()
  const [copied, setCopied] = React.useState(false)
  const supported = typeof navigator !== "undefined" && !!navigator.clipboard
  if (!supported) return null
  return (
    <Button
      type="button"
      variant="outline"
      size="lg"
      aria-label={`${t("ingest.copy")}: ${label}`}
      onClick={() => {
        navigator.clipboard
          .writeText(value)
          .then(() => setCopied(true))
          .catch(() => setCopied(false))
      }}
    >
      {copied ? <Check aria-hidden className="size-4" /> : <Copy aria-hidden className="size-4" />}
      {copied ? t("ingest.copied") : t("ingest.copy")}
    </Button>
  )
}

function AddressRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="module-row space-y-1.5 p-3">
      <p className="text-sm font-semibold">{label}</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all font-mono text-sm">{value}</code>
        <CopyButton value={value} label={label} />
      </div>
    </div>
  )
}

/** Where OBS or a phone app sends video. The destination keys (YouTube, Twitch) never appear here. */
export function IngestCard({ rtmpUrl, srtUrl, pairing = null, qrSrc = null }: IngestCardProps) {
  const { t } = useLanguage()
  const [showKey, setShowKey] = React.useState(false)
  const rtmp = rtmpUrl ?? pairing?.rtmp.url ?? null
  const srt = srtUrl ?? pairing?.srt.url ?? null
  const key = pairing?.rtmp.key

  return (
    <section className="module space-y-4 p-4 md:p-5" aria-labelledby="ingest-title">
      <div className="space-y-1">
        <h2 id="ingest-title" className="font-display text-xl font-bold">
          {t("ingest.title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("ingest.desc")}</p>
      </div>
      {!rtmp && !srt ? (
        <div className="module-row flex items-center gap-3 p-3">
          <span aria-hidden className="led text-muted-foreground" />
          <p className="text-sm text-muted-foreground">{t("ingest.unavailable")}</p>
        </div>
      ) : null}
      {rtmp ? <AddressRow label={t("ingest.rtmp")} value={rtmp} /> : null}
      {srt ? <AddressRow label={t("ingest.srt")} value={srt} /> : null}
      {key ? (
        <div className="module-row space-y-1.5 p-3">
          <p className="text-sm font-semibold">{t("ingest.key")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all font-mono text-sm" data-testid="ingest-key">
              {showKey ? key : "••••••••"}
            </code>
            <Button type="button" variant="outline" size="lg" aria-pressed={showKey} onClick={() => setShowKey((v) => !v)}>
              {showKey ? <EyeOff aria-hidden className="size-4" /> : <Eye aria-hidden className="size-4" />}
              {showKey ? t("ingest.keyHide") : t("ingest.keyShow")}
            </Button>
          </div>
        </div>
      ) : null}
      {qrSrc ? (
        <figure className="module-row flex flex-col items-center gap-2 p-4 text-center sm:flex-row sm:text-left">
          {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL of a QR code, not a stream image */}
          <img src={qrSrc} alt={t("ingest.qrAlt")} width={192} height={192} className="size-40 shrink-0 rounded-md bg-white p-2" />
          <figcaption className="text-sm text-muted-foreground">{t("ingest.qrHint")}</figcaption>
        </figure>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("ingest.privacy")}</p>
    </section>
  )
}
