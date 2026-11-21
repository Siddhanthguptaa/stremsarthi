"use client"

import { useLanguage } from "@/components/shell/language-provider"
import { lt, type LandingKey } from "@/lib/landing-text"
import { cn } from "@/lib/utils"

type Modality = "video" | "audio" | "chat"

/** Block positions along each lane's track (percent from "in" to "on air"). Decoration only. */
const LANES: { m: Modality; count: number; blocks: { at: number; w: number }[] }[] = [
  {
    m: "video",
    count: 6,
    blocks: [
      { at: 6, w: 9 },
      { at: 24, w: 12 },
      { at: 48, w: 7 },
      { at: 66, w: 14 },
      { at: 86, w: 8 },
    ],
  },
  {
    m: "audio",
    count: 4,
    blocks: [
      { at: 14, w: 4 },
      { at: 38, w: 5 },
      { at: 57, w: 4 },
      { at: 80, w: 6 },
    ],
  },
  {
    m: "chat",
    count: 5,
    blocks: [
      { at: 10, w: 6 },
      { at: 33, w: 8 },
      { at: 72, w: 6 },
    ],
  },
]

const TEXT: Record<Modality, string> = { video: "text-video", audio: "text-audio", chat: "text-chat" }
const BG: Record<Modality, string> = { video: "bg-video", audio: "bg-audio", chat: "bg-chat" }

export function Lanes() {
  const { lang } = useLanguage()
  const other = (m: Modality) => lt(lang, `lanes.${m}.hi` as LandingKey)
  return (
    <section aria-labelledby="lanes-title" className="mt-16 md:mt-24">
      <header className="max-w-2xl">
        <h2 id="lanes-title" className="text-4xl font-extrabold leading-[1.05] md:text-5xl">
          {lt(lang, "lanes.title")}
        </h2>
        <p className="mt-2 text-base text-muted-foreground">{lt(lang, "lanes.desc")}</p>
      </header>

      <div className="module relative mt-8 overflow-hidden">
        {/* ruler: in on the left, on air on the right, over the track column */}
        <div className="hidden border-b px-5 py-2.5 md:grid md:grid-cols-[9.5rem_minmax(0,1fr)_minmax(0,1.5fr)] md:gap-6">
          <span />
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{lt(lang, "lanes.in")}</span>
            <span className="inline-flex items-center gap-2 font-semibold text-foreground">
              {lt(lang, "lanes.out")}
              <span className="led text-tally" />
            </span>
          </div>
          <span />
        </div>

        {LANES.map(({ m, count, blocks }, li) => (
          <div
            key={m}
            className={cn(
              "grid gap-x-6 gap-y-4 px-4 py-6 sm:px-5 md:grid-cols-[9.5rem_minmax(0,1fr)_minmax(0,1.5fr)] md:items-center",
              li > 0 && "border-t",
            )}
          >
            <div className="flex items-baseline gap-3 md:block">
              <h3 className={cn("text-4xl font-extrabold leading-none md:text-5xl", TEXT[m])}>
                {lt(lang, `lanes.${m}` as LandingKey)}
              </h3>
              <p className="text-base text-muted-foreground md:mt-1" lang={lang === "en" ? "hi" : "en"}>
                {other(m)}
              </p>
            </div>

            <div aria-hidden>
              <div className="module-row relative h-14 overflow-hidden">
                <span className="absolute inset-x-3 top-1/2 h-px bg-border" />
                {blocks.map((b) => (
                  <span
                    key={b.at}
                    className={cn("mosaic absolute top-2.5 h-9 rounded-sm", `mosaic-${m}`)}
                    style={{ left: `${b.at}%`, width: `${b.w}%` }}
                  />
                ))}
                <span className="absolute inset-y-0 right-0 w-1 bg-tally" />
              </div>
              <div className="mt-1 flex justify-between text-xs text-muted-foreground md:hidden">
                <span>{lt(lang, "lanes.in")}</span>
                <span>{lt(lang, "lanes.out")}</span>
              </div>
            </div>

            <ul className="grid gap-x-6 gap-y-2 text-base lg:grid-cols-1">
              {Array.from({ length: count }, (_, i) => (
                <li key={i} className="flex gap-3">
                  <span aria-hidden className={cn("mt-2.5 size-1.5 shrink-0 rounded-[1px]", BG[m])} />
                  <span>{lt(lang, `lanes.${m}.${i + 1}` as LandingKey)}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}
