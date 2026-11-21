"use client"

import * as React from "react"

import { clipText } from "@/lib/chat-state"
import { categoryKey, formatPercent, formatTime } from "@/lib/format"
import type { I18nKey, Lang } from "@/lib/i18n"
import type { ModActionKind, QueueEntry, TimeoutPreset } from "@/lib/mod-state"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"
import { ActionBar } from "@/components/mod/action-bar"

interface QueueItemProps {
  entry: QueueEntry
  selected: boolean
  preset: TimeoutPreset
  lang: Lang
  onSelect: (key: string) => void
  onAct: (entry: QueueEntry, action: ModActionKind) => void
}

/** One flagged message: text (this screen only), categories, confidence, platform-result badge and actions. */
export const QueueItem = React.memo(function QueueItem({ entry, selected, preset, lang, onSelect, onAct }: QueueItemProps) {
  const { t } = useLanguage()
  const iso = new Date(entry.enqueuedAt * 1000).toISOString()
  const result = entry.platformResult
  return (
    <li
      data-key={entry.key}
      data-selected={selected ? "true" : "false"}
      aria-current={selected ? "true" : undefined}
      tabIndex={-1}
      onClick={() => onSelect(entry.key)}
      className={cn("space-y-3 rounded-lg border bg-card p-3 outline-none focus-visible:ring-2 focus-visible:ring-ring", selected && "ring-2 ring-primary")}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">{entry.author}</span>
        <span className="rounded border px-1.5 text-xs">{t(`chat.platform.${entry.platform}` as I18nKey)}</span>
        <time dateTime={iso} className="text-xs text-muted-foreground">
          {formatTime(iso, lang)}
        </time>
        <span className="ml-auto tabular-nums">
          <span className="sr-only">{t("alerts.confidence")} </span>
          {formatPercent(entry.confidence)}
        </span>
      </div>
      <p className="whitespace-pre-wrap break-words rounded-md bg-muted p-2 leading-snug" data-testid="mod-text">
        {clipText(entry.text)}
      </p>
      <ul className="flex flex-wrap gap-1.5" aria-label={t("mod.filter.category")}>
        {entry.categories.map((c) => (
          <li key={c} className="rounded-full border px-2.5 py-0.5 text-xs font-medium">
            {t(categoryKey(c))}
          </li>
        ))}
        <li className="rounded-full border px-2.5 py-0.5 text-xs">{t(`mod.verdict.${entry.action}` as I18nKey)}</li>
      </ul>
      {entry.staff ? <p className="text-sm text-muted-foreground">{t("mod.staff")}</p> : null}
      {result !== "pending" ? (
        <p role="status" className={cn("text-sm font-medium", result === "failed" && "text-destructive")} data-result={result}>
          {t(`mod.result.${result}` as I18nKey)}
        </p>
      ) : null}
      {entry.busy ? (
        <p role="status" className="text-sm text-muted-foreground">
          {t("mod.working")}
        </p>
      ) : null}
      <ActionBar entry={entry} preset={preset} onAct={onAct} />
    </li>
  )
})
