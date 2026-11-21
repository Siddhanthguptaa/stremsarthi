"use client"

import * as React from "react"

import { type I18nKey, type Lang } from "@/lib/i18n"
import { reportText, type ReportTextKey } from "@/lib/screen-text"
import { CATEGORIES } from "@/lib/wire"
import { useLanguage } from "@/components/shell/language-provider"

const KNOWN_CATEGORIES = new Set<string>(CATEGORIES)
const KNOWN_ACTIONS = new Set(["masked", "muted", "hidden", "held", "deleted", "timed_out", "warned", "failsafe"])
const KNOWN_MODALITIES = new Set(["video", "audio", "chat", "guard", "system"])
const KNOWN_LEVELS = new Set(["green", "yellow", "orange", "red"])

export interface ReportText {
  lang: Lang
  /** Report-screen text with `{n}`-style placeholders. */
  tr: (key: ReportTextKey, vars?: Record<string, string | number>) => string
  category: (code: string) => string
  action: (code: string) => string
  modality: (code: string) => string
  level: (code: string) => string
}

/** Labels for the report: shared names from `lib/i18n.ts` where they exist, the plain code otherwise. */
export function useReportText(): ReportText {
  const { lang, t } = useLanguage()
  return React.useMemo<ReportText>(
    () => ({
      lang,
      tr: (key, vars) => reportText(lang, key, vars),
      category: (code) => (KNOWN_CATEGORIES.has(code) ? t(`category.${code}` as I18nKey) : code),
      action: (code) => (KNOWN_ACTIONS.has(code) ? t(`safety.${code}` as I18nKey) : code),
      modality: (code) => (KNOWN_MODALITIES.has(code) ? reportText(lang, `report.modality.${code}` as ReportTextKey) : code),
      level: (code) => (KNOWN_LEVELS.has(code) ? reportText(lang, `report.level.${code}` as ReportTextKey) : code),
    }),
    [lang, t],
  )
}
