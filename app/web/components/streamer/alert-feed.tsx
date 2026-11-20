"use client"

import * as React from "react"

import { categoryKey, formatPercent, formatTime, safetyActionKey } from "@/lib/format"
import { type Alert, MAX_ALERTS } from "@/lib/live-state"
import type { Lang } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"

interface AlertFeedProps {
  alerts: readonly Alert[]
  total: number
}

/**
 * Live alerts from `safety.event`: category, time and confidence only. No image, box or text is ever shown.
 * The list is capped at 200 rows, newest first, so a burst cannot slow the page down.
 */
export function AlertFeed({ alerts, total }: AlertFeedProps) {
  const { t, lang } = useLanguage()
  return (
    <section aria-labelledby="alerts-heading" className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="alerts-heading" className="text-lg font-semibold">
          {t("feed.alerts")}
        </h2>
        <span className="text-sm tabular-nums text-muted-foreground">{total}</span>
      </div>
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
        {alerts.length === 0 ? (
          <p className="px-2 py-4 text-center text-sm text-muted-foreground">{t("alerts.empty")}</p>
        ) : (
          <ol className="space-y-2">
            {alerts.map((alert) => (
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
  return (
    <li className="flex items-center justify-between gap-3 rounded-md border bg-background px-3 py-2" data-category={alert.category}>
      <div className="min-w-0">
        <p className="font-medium leading-snug">{t(categoryKey(alert.category))}</p>
        <p className="text-sm text-muted-foreground">
          {t(safetyActionKey(alert.action))} · <time dateTime={alert.at}>{formatTime(alert.at, lang)}</time>
        </p>
      </div>
      <p className="shrink-0 text-right text-sm tabular-nums">
        <span className="sr-only">{t("alerts.confidence")} </span>
        <span className="text-base font-semibold">{formatPercent(alert.confidence)}</span>
      </p>
    </li>
  )
})
