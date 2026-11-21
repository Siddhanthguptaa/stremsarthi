"use client"

import { Captions } from "lucide-react"

import type { CaptionLine } from "@/lib/captions"
import { cn } from "@/lib/utils"
import { useDemoText } from "@/components/demo/use-demo-text"

const LANGUAGE_BADGE: Record<string, string> = {
  en: "EN",
  hi: "HI",
}

/**
 * A large caption strip under the stage: the last lines of what was said, in the caption language (English or
 * Hindi among others). Words the engine muted arrive already blanked. The text stays in memory only.
 */
export function CaptionStrip({ lines, large = false }: { lines: readonly CaptionLine[]; large?: boolean }) {
  const { d } = useDemoText()
  const last = lines[lines.length - 1]
  const badge = last ? (LANGUAGE_BADGE[last.lang] ?? last.lang.toUpperCase()) : null

  return (
    <section
      aria-label={d("captions.region")}
      data-testid="caption-strip"
      className={cn("flex items-center gap-4 rounded-xl border-2 bg-black px-4 py-2 text-white", large ? "min-h-32" : "min-h-20")}
    >
      <div className="flex shrink-0 flex-col items-center gap-1 text-xs font-bold">
        <Captions aria-hidden className="size-7" />
        {badge ? <span className="rounded bg-white/20 px-2 py-0.5 text-sm">{badge}</span> : null}
      </div>
      {/* Captions change many times a second: they are not announced, the strip is labelled for orientation. */}
      <div aria-live="off" className="min-w-0 flex-1">
        {lines.length === 0 ? (
          <p className="text-lg text-white/70">{d("captions.empty")}</p>
        ) : (
          <p lang={last?.lang} className={cn("space-y-1 font-semibold leading-snug", large ? "text-3xl lg:text-5xl" : "text-xl sm:text-2xl")}>
            {lines.map((line) => (
              <span key={line.id} className={line.final ? "block" : "block text-white/70"}>
                {line.text}
              </span>
            ))}
          </p>
        )}
      </div>
    </section>
  )
}
