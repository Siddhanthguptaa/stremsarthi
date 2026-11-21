"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  FileText,
  type LucideIcon,
  MonitorPlay,
  Presentation,
  Radio,
  ScanFace,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { type I18nKey, LANGS } from "@/lib/i18n"
import { useLatestReportId } from "@/components/report/use-latest-report"
import { useLanguage } from "@/components/shell/language-provider"
import { Wordmark } from "@/components/shell/wordmark"

const NAV: { href: string; key: I18nKey; icon: LucideIcon }[] = [
  { href: "/go-live", key: "nav.goLive", icon: Radio },
  { href: "/enrol", key: "nav.enrol", icon: ScanFace },
  { href: "/controls", key: "nav.controls", icon: SlidersHorizontal },
  { href: "/live", key: "nav.live", icon: MonitorPlay },
  { href: "/demo", key: "nav.demo", icon: Presentation },
  { href: "/mod", key: "nav.moderator", icon: ShieldCheck },
]

/**
 * The app shell's chrome. One nav, two shapes:
 * - md and up: a fixed left rail (brand on top, nav, language switch at the bottom).
 * - phones: a top bar (brand + language) and the same nav as a fixed bottom tab bar.
 */
export function AppHeader() {
  const { lang, setLang, t } = useLanguage()
  const pathname = usePathname()
  // The "Safety report" link appears once there is a session with a report (and the token may read reports).
  const reportId = useLatestReportId()
  const items = reportId ? [...NAV, { href: `/report/${reportId}`, key: "nav.report" as const, icon: FileText }] : NAV

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b bg-background px-4 py-2.5 md:fixed md:inset-y-0 md:left-0 md:w-60 md:flex-col md:items-stretch md:justify-start md:gap-0 md:border-b-0 md:border-r md:bg-card md:px-3 md:py-5">
      <Link href="/" className="flex min-h-11 items-center rounded-md md:px-2" aria-label={t("brand.name")}>
        <Wordmark label={t("brand.name")} />
      </Link>
      <p className="hidden px-2 pt-1 text-xs leading-snug text-muted-foreground md:block">{t("brand.tagline")}</p>

      <nav
        aria-label={t("nav.label")}
        className="fixed inset-x-0 bottom-0 z-40 border-t bg-card pb-[env(safe-area-inset-bottom)] md:static md:mt-8 md:flex-1 md:border-t-0 md:bg-transparent md:pb-0"
      >
        <ul className="flex h-[var(--bottom-nav-h)] overflow-x-auto md:h-auto md:flex-col md:gap-1 md:overflow-visible">
          {items.map(({ href, key, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`)
            return (
              <li key={href} className="min-w-[4.5rem] flex-1 md:min-w-0 md:flex-none">
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "relative flex h-full flex-col items-center justify-center gap-0.5 px-2 text-[0.7rem] font-semibold md:min-h-11 md:flex-row md:justify-start md:gap-3 md:rounded-md md:px-3 md:text-sm",
                    active ? "text-primary md:bg-desk-raised md:text-foreground" : "text-muted-foreground hover:text-foreground md:hover:bg-desk-raised/60",
                  )}
                >
                  {active ? (
                    <span aria-hidden className="absolute inset-x-3 top-0 h-0.5 rounded-b bg-primary md:inset-x-auto md:inset-y-2 md:left-0 md:h-auto md:w-1 md:rounded-r md:rounded-bl-none" />
                  ) : null}
                  <Icon aria-hidden className={cn("size-5 shrink-0", active && "text-primary")} />
                  <span className="whitespace-nowrap">{t(key)}</span>
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>

      <div role="group" aria-label={t("lang.toggle")} className="flex shrink-0 overflow-hidden rounded-md border bg-background md:mx-2 md:self-start">
        {LANGS.map((code) => (
          <button
            key={code}
            type="button"
            onClick={() => setLang(code)}
            aria-pressed={lang === code}
            className={cn(
              "min-h-11 min-w-11 px-3 text-sm font-semibold",
              lang === code ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t(code === "en" ? "lang.en" : "lang.hi")}
          </button>
        ))}
      </div>
    </header>
  )
}
