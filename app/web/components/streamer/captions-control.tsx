"use client"

import * as React from "react"
import { CheckCircle2, Captions, Loader2 } from "lucide-react"

import type { ApiClient } from "@/lib/api"
import {
  CAPTION_LANGUAGES,
  buildCaptionsPatch,
  captionsFromConfig,
  type CaptionLine,
  type CaptionsSetting,
} from "@/lib/captions"
import type { I18nKey } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"

export type CaptionsApi = Pick<ApiClient, "getConfig" | "patchConfig">

interface CaptionsControlProps {
  api: CaptionsApi
  /** The latest caption lines from the live stream (memory only). */
  lines?: readonly CaptionLine[]
}

type Notice = "saved" | "failed" | null

/** Captions on/off and language. Applies at once (`PATCH /api/config`) and goes back if the API refuses. */
export function CaptionsControl({ api, lines = [] }: CaptionsControlProps) {
  const { t } = useLanguage()
  const id = React.useId()
  const [setting, setSetting] = React.useState<CaptionsSetting | null>(null)
  const [loadFailed, setLoadFailed] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [notice, setNotice] = React.useState<Notice>(null)

  React.useEffect(() => {
    let alive = true
    api
      .getConfig()
      .then((config) => alive && setSetting(captionsFromConfig(config)))
      .catch(() => alive && setLoadFailed(true))
    return () => {
      alive = false
    }
  }, [api])

  React.useEffect(() => {
    if (notice !== "saved") return
    const timer = setTimeout(() => setNotice(null), 2000)
    return () => clearTimeout(timer)
  }, [notice])

  const apply = async (change: Partial<CaptionsSetting>) => {
    if (!setting) return
    const before = setting
    setSetting({ ...before, ...change })
    setSaving(true)
    setNotice(null)
    try {
      setSetting(captionsFromConfig(await api.patchConfig(buildCaptionsPatch(change))))
      setNotice("saved")
    } catch {
      setSetting(before)
      setNotice("failed")
    } finally {
      setSaving(false)
    }
  }

  const options = React.useMemo(() => {
    const codes: string[] = [...CAPTION_LANGUAGES]
    if (setting && !codes.includes(setting.language)) codes.push(setting.language)
    return codes
  }, [setting])
  const langLabel = (code: string) => {
    const key = `captions.lang.${code}` as I18nKey
    return t(key) === key ? code : t(key)
  }

  return (
    <section aria-labelledby={`${id}-title`} className="space-y-3 rounded-lg border bg-card p-4" data-testid="captions-control">
      <div className="space-y-1">
        <h2 id={`${id}-title`} className="flex items-center gap-2 text-lg font-semibold">
          <Captions aria-hidden className="size-5" />
          {t("captions.title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("captions.desc")}</p>
      </div>

      {loadFailed ? <p role="alert">{t("captions.unavailable")}</p> : null}

      {setting ? (
        <>
          <div className="flex min-h-11 items-center justify-between gap-3">
            <Label htmlFor={`${id}-on`} className="text-base">
              {t("captions.toggle")}
            </Label>
            <Switch id={`${id}-on`} checked={setting.enabled} disabled={saving} onCheckedChange={(enabled) => void apply({ enabled })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-lang`}>{t("captions.language")}</Label>
            <Select
              id={`${id}-lang`}
              value={setting.language}
              disabled={saving}
              onChange={(event) => void apply({ language: event.target.value })}
            >
              {options.map((code) => (
                <option key={code} value={code}>
                  {langLabel(code)}
                </option>
              ))}
            </Select>
          </div>
        </>
      ) : loadFailed ? null : (
        <Loader2 aria-hidden className="size-5 animate-spin motion-reduce:animate-none" />
      )}

      <div role="status" aria-live="polite" className="min-h-6 text-sm">
        {notice === "saved" ? (
          <span className="inline-flex items-center gap-1.5 text-success">
            <CheckCircle2 aria-hidden className="size-4" />
            {t("captions.saved")}
          </span>
        ) : null}
        {notice === "failed" ? <span className="text-destructive">{t("captions.failed")}</span> : null}
      </div>

      {setting ? (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">{t("captions.preview")}</h3>
          {!setting.enabled ? (
            <p className="text-sm text-muted-foreground">{t("captions.off")}</p>
          ) : lines.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("captions.waiting")}</p>
          ) : (
            <p lang={setting.language} className="rounded-md border bg-background px-3 py-2 text-base leading-relaxed" data-testid="caption-lines">
              {lines.map((line) => (
                <span key={line.id} className={line.final ? "block" : "block text-muted-foreground"}>
                  {line.text}
                </span>
              ))}
            </p>
          )}
        </div>
      ) : null}
    </section>
  )
}
