"use client"

import * as React from "react"

import { categoryKey, formatPercent, formatTime, safetyActionKey } from "@/lib/format"
import { ALERT_FILTERS, type Alert, type AlertFilter, filterAlerts, MAX_ALERTS } from "@/lib/live-state"
import type { I18nKey, Lang } from "@/lib/i18n"
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

/**
 * Live alerts from `safety.event`: category, time and confidence only. No image, box or text is ever shown.
 * The list is capped at 200 rows, newest first, so a burst cannot slow the page down.
 */
export function AlertFeed({ alerts, total, loadFailed = false }: AlertFeedProps) {
  const { t, lang } = useLanguage()
  const [filter, setFilter] = React.useState<AlertFilter>("all")
  const shown = filterAlerts(alerts, filter)
  return (
    <section aria-labelledby="alerts-heading" className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="alerts-heading" className="text-lg font-semibold">
          {t("feed.alerts")}
        </h2>
        <span className="text-sm tabular-nums text-muted-foreground">{total}</span>
      </div>
      <div role="group" aria-label={t("alerts.filter.label")} className="flex flex-wrap gap-2">
        {ALERT_FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            className="min-h-11 rounded-full border px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:border-primary aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          >
            {t(FILTER_KEYS[f])}
          </button>
        ))}
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
        className="max-h-[45vh] min-h-24 overflow-y-auto rounded-lg border bg-card p-2"
        data-testid="alert-feed"
      >
        {shown.length === 0 ? (
          <p className="px-2 py-4 text-center text-sm text-muted-foreground">
            {alerts.length === 0 ? t("alerts.empty") : t("alerts.filtered")}
          </p>
        ) : (
          <ol className="space-y-2">
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
    <li className="flex items-center justify-between gap-3 rounded-md border bg-background px-3 py-2" data-category={alert.category} data-modality={alert.modality}>
      <div className="min-w-0">
        <p className="font-medium leading-snug">{t(categoryKey(alert.category))}</p>
        <p className="text-sm text-muted-foreground">
          {t(MODALITY_KEYS[alert.modality])} · {t(safetyActionKey(alert.action))} ·{" "}
          <time dateTime={alert.at}>{formatTime(alert.at, lang)}</time>
          {seconds === null ? null : ` · ${t("alerts.duration").replace("{seconds}", seconds)}`}
        </p>
      </div>
      <p className="shrink-0 text-right text-sm tabular-nums">
        <span className="sr-only">{t("alerts.confidence")} </span>
        <span className="text-base font-semibold">{formatPercent(alert.confidence)}</span>
      </p>
    </li>
  )
})
