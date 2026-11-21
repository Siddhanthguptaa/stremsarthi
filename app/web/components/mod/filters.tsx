"use client"

import { categoryKey } from "@/lib/format"
import type { I18nKey } from "@/lib/i18n"
import type { ModFilters } from "@/lib/mod-state"
import type { Category, Platform } from "@/lib/wire"
import { useLanguage } from "@/components/shell/language-provider"
import { Segmented } from "@/components/mod/segmented"

const PLATFORMS: readonly (Platform | "all")[] = ["all", "youtube", "twitch"]

interface FiltersProps {
  filters: ModFilters
  categories: readonly Category[]
  onChange: (next: ModFilters) => void
}

/** Platform and category filters for the queue: two segmented controls (toggle buttons, text and state, not colour alone). */
export function Filters({ filters, categories, onChange }: FiltersProps) {
  const { t } = useLanguage()
  const platformLabel = (p: Platform | "all"): string => (p === "all" ? t("mod.filter.all") : t(`chat.platform.${p}` as I18nKey))
  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
      <Segmented
        label={t("mod.filter.platform")}
        options={PLATFORMS.map((p) => ({ value: p, label: platformLabel(p) }))}
        value={filters.platform}
        onChange={(platform) => onChange({ ...filters, platform })}
      />
      <Segmented
        label={t("mod.filter.category")}
        options={[{ value: "all" as const, label: t("mod.filter.all") }, ...categories.map((c) => ({ value: c as Category | "all", label: t(categoryKey(c)) }))]}
        value={filters.category}
        onChange={(category) => onChange({ ...filters, category })}
      />
    </div>
  )
}
