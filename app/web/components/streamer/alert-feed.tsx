"use client"

import * as React from "react"

import { categoryKey, formatPercent, formatTime, safetyActionKey } from "@/lib/format"
import { ALERT_FILTERS, type Alert, type AlertFilter, filterAlerts, MAX_ALERTS } from "@/lib/live-state"
import type { I18nKey, Lang } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"

interface AlertFeedProps {
  alerts: readonly Alert[]
  total: number
  /** Earlier alerts could not be loaded over REST (live ones still arrive). */
  loadFailed?: boolean
}

const FILTER_KEYS: Record<AlertFilter, I18nKey> = {
  all: "alerts.filter.all",
  video: "alerts.filter.video",
  audio: "alerts.filter.audio",
  chat: "alerts.filter.chat",
}
const MODALITY_KEYS: Record<Alert["modality"], I18nKey> = {
  video: "alerts.modality.video",
  audio: "alerts.modality.audio",
  chat: "alerts.modality.chat",
  guard: "alerts.modality.guard",
  system: "alerts.modality.system",
}
const LED: Record<Alert["modality"], string> = {
  video: "text-video",
  audio: "text-audio",
  chat: "text-chat",
  guard: "text-warning",
  system: "text-muted-foreground",
}

/**
 * Live alerts from `safety.event` as a timeline: time, source LED, category, action, confidence. No image, box
 * or text is ever shown. The list is capped at 200 rows, newest first, so a burst cannot slow the page down.
 */
export function AlertFeed({ alerts, total, loadFailed = false }: AlertFeedProps) {
  const { t, lang } = useLanguage()
  const [filter, setFilter] = React.useState<AlertFilter>("all")
  const shown = filterAlerts(alerts, filter)
  return (
    <section aria-labelledby="alerts-heading" className="module space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h2 id="alerts-heading" className="flex items-baseline gap-3 text-xl font-bold">
          {t("feed.alerts")}
          <span className="timecode text-lg text-muted-foreground">{total}</span>
        </h2>
        <div role="group" aria-label={t("alerts.filter.label")} className="inline-flex rounded-md bg-background/60 p-1">
          {ALERT_FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                "min-h-11 min-w-14 rounded px-3 text-sm font-semibold text-muted-foreground hover:text-foreground",
                "aria-pressed:bg-primary aria-pressed:text-primary-foreground"
              )}
            >
              {t(FILTER_KEYS[f])}
            </button>
          ))}
        </div>
      </div>
      {loadFailed ? (
        <p role="status" className="text-sm text-muted-foreground">
          {t("alerts.loadFailed")}
        </p>
      ) : null}
      <div
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-labelledby="alerts-heading"
        // Scrollable regions must be keyboard reachable.
        tabIndex={0}
        className="module-row max-h-[45vh] min-h-24 overflow-y-auto p-1.5"
        data-testid="alert-feed"
      >
        {shown.length === 0 ? (
          <p className="px-2 py-4 text-center text-sm text-muted-foreground">
            {alerts.length === 0 ? t("alerts.empty") : t("alerts.filtered")}
          </p>
        ) : (
          <ol className="divide-y divide-border">
            {shown.map((alert) => (
              <AlertRow key={alert.id} alert={alert} lang={lang} />
            ))}
          </ol>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {t("alerts.privacy")}
        {total > MAX_ALERTS ? ` ${t("alerts.capped")}` : ""}
      </p>
    </section>
  )
}

const AlertRow = React.memo(function AlertRow({ alert, lang }: { alert: Alert; lang: Lang }) {
  const { t } = useLanguage()
  const seconds = alert.durationMs === null ? null : (alert.durationMs / 1000).toFixed(1)
  return (
    <li className="grid grid-cols-[4.75rem_1fr_auto] items-center gap-x-3 px-2 py-2" data-category={alert.category} data-modality={alert.modality}>
      <time dateTime={alert.at} className="timecode text-base text-muted-foreground">
        {formatTime(alert.at, lang)}
      </time>
      <div className="min-w-0">
        <p className="font-medium leading-snug">{t(categoryKey(alert.category))}</p>
        <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
          <span aria-hidden className={cn("led size-2", LED[alert.modality])} />
          <span>{t(MODALITY_KEYS[alert.modality])}</span>
          <span aria-hidden>·</span>
          <span>{t(safetyActionKey(alert.action))}</span>
          {seconds === null ? null : <span>{`· ${t("alerts.duration").replace("{seconds}", seconds)}`}</span>}
        </p>
      </div>
      <p className="text-right">
        <span className="sr-only">{t("alerts.confidence")} </span>
        <span className="timecode text-lg font-semibold">{formatPercent(alert.confidence)}</span>
      </p>
    </li>
  )
})
