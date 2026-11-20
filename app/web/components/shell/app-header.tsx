"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Radio } from "lucide-react"

import { cn } from "@/lib/utils"
import { type I18nKey, LANGS } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"

const NAV: { href: string; key: I18nKey }[] = [
  { href: "/go-live", key: "nav.goLive" },
  { href: "/enrol", key: "nav.enrol" },
  { href: "/controls", key: "nav.controls" },
  { href: "/live", key: "nav.live" },
  { href: "/mod", key: "nav.moderator" },
]

export function AppHeader() {
  const { lang, setLang, t } = useLanguage()
  const pathname = usePathname()

  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 px-4 py-3">
        <Link href="/" className="flex min-h-11 items-center gap-2 rounded-md font-semibold">
          <Radio aria-hidden className="size-6 text-primary" />
          <span className="leading-tight">
            {t("brand.name")}
            <span className="block text-xs font-normal text-muted-foreground">{t("brand.tagline")}</span>
          </span>
        </Link>
        <div role="group" aria-label={t("lang.toggle")} className="flex shrink-0 overflow-hidden rounded-md border border-input">
          {LANGS.map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => setLang(code)}
              aria-pressed={lang === code}
              className={cn(
                "min-h-11 min-w-11 px-3 text-sm font-medium",
                lang === code ? "bg-primary text-primary-foreground" : "bg-card text-foreground hover:bg-muted",
              )}
            >
              {t(code === "en" ? "lang.en" : "lang.hi")}
            </button>
          ))}
        </div>
      </div>
      <nav aria-label={t("nav.label")} className="mx-auto w-full max-w-5xl overflow-x-auto px-4 pb-2">
        <ul className="flex gap-2">
          {NAV.map(({ href, key }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`)
            return (
              <li key={href} className="shrink-0">
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "inline-flex min-h-11 items-center rounded-full border px-4 text-sm font-medium",
                    active ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
                  )}
                >
                  {t(key)}
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>
    </header>
  )
}
