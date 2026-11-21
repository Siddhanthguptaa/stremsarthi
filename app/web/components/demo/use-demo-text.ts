"use client"

import * as React from "react"

import { type DemoKey, demoText } from "@/lib/demo-i18n"
import { type DeskKey, deskText } from "@/lib/i18n-demo-desk"
import { useLanguage } from "@/components/shell/language-provider"

/** `d(key)` in the current language, and `both(key)` as `[current, other]` for bilingual labels. */
export function useDemoText() {
  const { lang } = useLanguage()
  return React.useMemo(() => {
    const other = lang === "en" ? "hi" : "en"
    return {
      lang,
      other,
      d: (key: DemoKey) => demoText(lang, key),
      alt: (key: DemoKey) => demoText(other, key),
      desk: (key: DeskKey) => deskText(lang, key),
    }
  }, [lang])
}
