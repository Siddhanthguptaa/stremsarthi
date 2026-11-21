"use client"

import { Radio } from "lucide-react"

import type { SessionView } from "@/lib/api-streamer"
import { bannerKind, glText, PLATFORM_NAME } from "@/lib/golive-text"
import { useLanguage } from "@/components/shell/language-provider"

/** The unmistakable "LIVE to <platform>" bar (red only while the stream really goes to a platform). */
export function LiveBanner({ session }: { session: Pick<SessionView, "state" | "output" | "destinations"> | null }) {
  const { lang } = useLanguage()
  const kind = bannerKind(session)
  const names = (session?.destinations ?? []).map((d) => PLATFORM_NAME[d]).join(" + ")
  const tone =
    kind === "live"
      ? "border-red-600 bg-red-600 text-white"
      : kind === "waiting"
        ? "border-amber-500 bg-amber-500/15"
        : "border-border bg-muted"
  const title =
    kind === "live" ? glText(lang, "live.banner.live", { names: names || "?" }) : glText(lang, `live.banner.${kind === "waiting" ? "waiting" : kind}` as "live.banner.none")
  const sub =
    kind === "live" ? "live.banner.liveSub" : kind === "waiting" ? "live.banner.waitingSub" : kind === "rehearsal" ? "live.banner.rehearsalSub" : "live.banner.noneSub"
  return (
    <div role="status" aria-live="polite" data-testid="live-banner" data-kind={kind} className={`flex items-center gap-3 rounded-lg border-2 px-4 py-3 ${tone}`}>
      <Radio aria-hidden className={`size-6 shrink-0 ${kind === "live" ? "animate-pulse motion-reduce:animate-none" : ""}`} />
      <div className="min-w-0">
        <p className="text-xl font-extrabold uppercase tracking-wide">{title}</p>
        <p className="text-sm opacity-90">{glText(lang, sub)}</p>
      </div>
    </div>
  )
}
