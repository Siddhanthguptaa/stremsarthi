"use client"

import type { CaptionLine } from "@/lib/captions"
import { cn } from "@/lib/utils"
import { useDemoText } from "@/components/demo/use-demo-text"

const LANGUAGE_BADGE: Record<string, string> = {
  en: "EN",
  hi: "HI",
}

/**
 * The caption lower-third: the last lines of what was said, in the caption language (English or Hindi among
 * others), drawn along the bottom of the program monitor. Words the engine muted arrive already blanked. The text
 * stays in memory only.
 */
export function CaptionStrip({ lines, large = false }: { lines: readonly CaptionLine[]; large?: boolean }) {
  const { d } = useDemoText()
  const last = lines[lines.length - 1]
  const badge = last ? (LANGUAGE_BADGE[last.lang] ?? last.lang.toUpperCase()) : null
  const shown = lines.slice(-2)

  return (
    <section
      aria-label={d("captions.region")}
      data-testid="caption-strip"
      className={cn("flex items-center gap-3 bg-black/80 px-3 text-white", lines.length === 0 ? "min-h-9 py-1" : large ? "min-h-24 py-3" : "min-h-14 py-2")}
    >
      {badge ? <span className="shrink-0 rounded-sm bg-white/20 px-2 py-0.5 text-xs font-bold">{badge}</span> : null}
      {/* Captions change many times a second: they are not announced, the strip is labelled for orientation. */}
      <div aria-live="off" className="min-w-0 flex-1">
        {lines.length === 0 ? (
          <p className="text-sm text-white/70">{d("captions.empty")}</p>
        ) : (
          <p lang={last?.lang} className={cn("space-y-0.5 font-semibold leading-snug", large ? "text-3xl lg:text-4xl" : "text-lg sm:text-2xl")}>
            {shown.map((line) => (
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
