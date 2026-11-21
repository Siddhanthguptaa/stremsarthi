"use client"

import { categoryKey } from "@/lib/format"
import type { I18nKey } from "@/lib/i18n"
import type { ModFilters } from "@/lib/mod-state"
import type { Category, Platform } from "@/lib/wire"
import { useLanguage } from "@/components/shell/language-provider"

const PLATFORMS: readonly (Platform | "all")[] = ["all", "youtube", "twitch"]
const chip =
  "min-h-11 shrink-0 rounded-full border px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:border-primary aria-pressed:bg-primary aria-pressed:text-primary-foreground"

interface FiltersProps {
  filters: ModFilters
  categories: readonly Category[]
  onChange: (next: ModFilters) => void
}

/** Platform and category filters for the queue (toggle buttons, text and state, not colour alone). */
export function Filters({ filters, categories, onChange }: FiltersProps) {
  const { t } = useLanguage()
  const platformLabel = (p: Platform | "all"): string => (p === "all" ? t("mod.filter.all") : t(`chat.platform.${p}` as I18nKey))
  return (
    <div className="space-y-2">
      <div role="group" aria-label={t("mod.filter.platform")} className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0">
        {PLATFORMS.map((p) => (
          <button key={p} type="button" className={chip} aria-pressed={filters.platform === p} onClick={() => onChange({ ...filters, platform: p })}>
            {platformLabel(p)}
          </button>
        ))}
      </div>
      <div role="group" aria-label={t("mod.filter.category")} className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0">
        <button type="button" className={chip} aria-pressed={filters.category === "all"} onClick={() => onChange({ ...filters, category: "all" })}>
          {t("mod.filter.all")}
        </button>
        {categories.map((c) => (
          <button key={c} type="button" className={chip} aria-pressed={filters.category === c} onClick={() => onChange({ ...filters, category: c })}>
            {t(categoryKey(c))}
          </button>
        ))}
      </div>
    </div>
  )
}
