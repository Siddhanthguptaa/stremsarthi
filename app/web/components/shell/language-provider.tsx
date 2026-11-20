"use client"

import * as React from "react"

import { type I18nKey, type Lang, loadLang, saveLang, translate } from "@/lib/i18n"

interface LanguageContextValue {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (key: I18nKey) => string
}

const LanguageContext = React.createContext<LanguageContextValue | null>(null)

// Tiny external store: the choice lives in localStorage (when allowed) and in memory otherwise.
const listeners = new Set<() => void>()
let chosen: Lang | null = null

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
const getSnapshot = (): Lang => chosen ?? loadLang()
const getServerSnapshot = (): Lang => "en" // server and first client render agree, so hydration matches

function setLang(next: Lang): void {
  chosen = next
  saveLang(next)
  listeners.forEach((listener) => listener())
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const lang = React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)

  React.useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  const value = React.useMemo<LanguageContextValue>(
    () => ({ lang, setLang, t: (key) => translate(lang, key) }),
    [lang],
  )
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useLanguage(): LanguageContextValue {
  const ctx = React.useContext(LanguageContext)
  if (!ctx) throw new Error("useLanguage must be used inside <LanguageProvider>")
  return ctx
}
