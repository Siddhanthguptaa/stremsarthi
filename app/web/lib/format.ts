/** Display helpers: times, percentages, label keys. Numbers use Latin digits in both languages. */

import type { I18nKey, Lang } from "@/lib/i18n"
import type { Category, HoldSource, SafetyAction } from "@/lib/wire"

const LOCALE: Record<Lang, string> = { en: "en-IN-u-nu-latn", hi: "hi-IN-u-nu-latn" }

/** `2026-11-20T14:03:09.123+00:00` -> `7:33:09 pm` in the viewer's time zone. "" for an unparseable value. */
export function formatTime(iso: string, lang: Lang): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  return new Intl.DateTimeFormat(LOCALE[lang], { hour: "numeric", minute: "2-digit", second: "2-digit" }).format(date)
}

/** 0.934 -> "93%". Clamped to 0..100. */
export function formatPercent(confidence: number): string {
  const pct = Math.round(Math.min(1, Math.max(0, confidence)) * 100)
  return `${pct}%`
}

export function formatSeconds(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "-" : value.toFixed(digits)
}

export const categoryKey = (category: Category): I18nKey => `category.${category}` as I18nKey
export const safetyActionKey = (action: SafetyAction): I18nKey => `safety.${action}` as I18nKey
export const holdSourceKey = (source: HoldSource): I18nKey => `hold.source.${source}` as I18nKey
