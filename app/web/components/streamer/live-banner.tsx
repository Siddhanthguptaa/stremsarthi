"use client"

import type { SessionView } from "@/lib/api-streamer"
import { bannerKind, glText, PLATFORM_NAME } from "@/lib/golive-text"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"

/** "LIVE to <platform>": where the stream goes. Tally red only while it really goes to a platform. */
export function LiveBanner({ session, className }: { session: Pick<SessionView, "state" | "output" | "destinations"> | null; className?: string }) {
  const { lang } = useLanguage()
  const kind = bannerKind(session)
  const names = (session?.destinations ?? []).map((d) => PLATFORM_NAME[d]).join(" + ")
  const title =
    kind === "live" ? glText(lang, "live.banner.live", { names: names || "?" }) : glText(lang, `live.banner.${kind === "waiting" ? "waiting" : kind}` as "live.banner.none")
  const sub =
    kind === "live" ? "live.banner.liveSub" : kind === "waiting" ? "live.banner.waitingSub" : kind === "rehearsal" ? "live.banner.rehearsalSub" : "live.banner.noneSub"
  return (
    <div role="status" aria-live="polite" data-testid="live-banner" data-kind={kind} className={cn("min-w-0", className)}>
      <p className="font-display text-xl font-bold leading-tight">{title}</p>
      <p className="text-sm text-muted-foreground">{glText(lang, sub)}</p>
    </div>
  )
}
