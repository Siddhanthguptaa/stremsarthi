"use client"

import * as React from "react"

import { categoryKey } from "@/lib/format"
import { type ProofState, proofRows } from "@/lib/demo-proof"
import type { DeskKey } from "@/lib/i18n-demo-desk"
import type { Modality } from "@/lib/wire"
import { cn } from "@/lib/utils"
import { useDemoText } from "@/components/demo/use-demo-text"
import { useLanguage } from "@/components/shell/language-provider"

interface ProofCounterProps {
  state: ProofState
  /** The slimmer card for the stage (no footnote, tighter chips) instead of the roomy one. */
  compact?: boolean
}

const SOURCES: { modality: Extract<Modality, "video" | "audio" | "chat">; led: string; label: DeskKey }[] = [
  { modality: "video", led: "text-video", label: "proof.video" },
  { modality: "audio", led: "text-audio", label: "proof.audio" },
  { modality: "chat", led: "text-chat", label: "proof.chat" },
]

/** The privacy-proof counter: a running total, one big number per source and a per-category count. Numbers only. */
export function ProofCounter({ state, compact = false }: ProofCounterProps) {
  const { t } = useLanguage()
  const { d, desk } = useDemoText()
  const headingId = React.useId()
  const rows = proofRows(state)

  return (
    <section aria-labelledby={headingId} data-testid="proof-counter" className={cn("module", compact ? "space-y-3 p-4" : "space-y-4 p-5")}>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id={headingId} className="text-xl font-bold leading-tight">
            {d("proof.title")}
          </h2>
          <p className="text-sm text-muted-foreground">{d("proof.total")}</p>
        </div>
        <span className="timecode text-6xl font-extrabold leading-none" data-testid="proof-total">
          {state.total}
        </span>
      </div>
      <div role="group" aria-label={desk("proof.byModality")} className="grid grid-cols-3 gap-2">
        {SOURCES.map(({ modality, led, label }) => (
          <div key={modality} data-modality={modality} className="module-row px-3 py-2">
            <p className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
              <span aria-hidden className={cn("led", led)} />
              {desk(label)}
            </p>
            <p className="timecode text-4xl font-bold leading-none">{state.byModality[modality] ?? 0}</p>
          </div>
        ))}
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{d("proof.empty")}</p>
      ) : (
        <ul aria-label={d("proof.count")} className="flex flex-wrap gap-1.5">
          {rows.map(({ category, count }) => (
            <li key={category} data-category={category} className="module-row inline-flex items-center gap-2 px-3 py-1 text-sm">
              <span className="font-medium">{t(categoryKey(category))}</span>
              <span className="timecode text-lg font-bold">{count}</span>
            </li>
          ))}
        </ul>
      )}
      {compact ? null : <p className="text-xs text-muted-foreground">{d("proof.note")}</p>}
    </section>
  )
}
