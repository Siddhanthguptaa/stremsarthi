"use client"

import { Inbox } from "lucide-react"

import type { Lang } from "@/lib/i18n"
import type { ModActionKind, QueueEntry, TimeoutPreset } from "@/lib/mod-state"
import { modText } from "@/lib/screen-text"
import { useLanguage } from "@/components/shell/language-provider"
import { QueueItem } from "@/components/mod/queue-item"
import { Button } from "@/components/ui/button"

interface QueueListProps {
  items: readonly QueueEntry[]
  /** The queue has items, but the filters hide them all. */
  filtered: boolean
  selectedKey: string | null
  preset: TimeoutPreset
  lang: Lang
  onSelect: (key: string) => void
  onAct: (entry: QueueEntry, action: ModActionKind) => void
  /** Offered in the "filters hide everything" state. */
  onClearFilters?: () => void
}

/** Newest first. A polite live log so a screen reader hears new arrivals. */
export function QueueList({ items, filtered, selectedKey, preset, lang, onSelect, onAct, onClearFilters }: QueueListProps) {
  const { t } = useLanguage()
  if (items.length === 0) {
    return (
      <div
        role="status"
        className="flex flex-col items-center gap-2 rounded-lg border bg-card px-4 py-8 text-center"
        data-testid="mod-empty"
      >
        <Inbox aria-hidden className="size-8 text-muted-foreground" />
        <p className="font-semibold">{filtered ? t("mod.emptyFiltered") : modText(lang, "modx.empty.title")}</p>
        <p className="max-w-prose text-sm text-muted-foreground">
          {filtered ? modText(lang, "modx.filtered.body") : t("mod.empty")}
        </p>
        {filtered && onClearFilters ? (
          <Button type="button" variant="outline" className="min-h-11" onClick={onClearFilters}>
            {modText(lang, "modx.filter.clear")}
          </Button>
        ) : null}
      </div>
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
