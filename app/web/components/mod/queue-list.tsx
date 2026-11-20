"use client"

import type { Lang } from "@/lib/i18n"
import type { ModActionKind, QueueEntry, TimeoutPreset } from "@/lib/mod-state"
import { useLanguage } from "@/components/shell/language-provider"
import { QueueItem } from "@/components/mod/queue-item"

interface QueueListProps {
  items: readonly QueueEntry[]
  /** The queue has items, but the filters hide them all. */
  filtered: boolean
  selectedKey: string | null
  preset: TimeoutPreset
  lang: Lang
  onSelect: (key: string) => void
  onAct: (entry: QueueEntry, action: ModActionKind) => void
}

/** Newest first. A polite live log so a screen reader hears new arrivals. */
export function QueueList({ items, filtered, selectedKey, preset, lang, onSelect, onAct }: QueueListProps) {
  const { t } = useLanguage()
  if (items.length === 0) {
    return (
      <p role="status" className="rounded-lg border bg-card px-3 py-6 text-center text-sm text-muted-foreground" data-testid="mod-empty">
        {filtered ? t("mod.emptyFiltered") : t("mod.empty")}
      </p>
    )
  }
  return (
    <div role="log" aria-live="polite" aria-relevant="additions" aria-label={t("mod.queue")} data-testid="mod-queue">
      <ol className="space-y-3">
        {items.map((entry) => (
          <QueueItem key={entry.key} entry={entry} selected={entry.key === selectedKey} preset={preset} lang={lang} onSelect={onSelect} onAct={onAct} />
        ))}
      </ol>
    </div>
  )
}
