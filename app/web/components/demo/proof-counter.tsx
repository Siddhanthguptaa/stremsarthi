"use client"

import * as React from "react"
import { ShieldCheck } from "lucide-react"

import { categoryKey } from "@/lib/format"
import { type ProofState, proofRows } from "@/lib/demo-proof"
import { useDemoText } from "@/components/demo/use-demo-text"
import { useLanguage } from "@/components/shell/language-provider"

interface ProofCounterProps {
  state: ProofState
  /** One short card for the stage (total and category chips) instead of the roomy list. */
  compact?: boolean
}

/** The privacy-proof counter: a running total and a per-category count. Categories and numbers only. */
export function ProofCounter({ state, compact = false }: ProofCounterProps) {
  const { t } = useLanguage()
  const { d } = useDemoText()
  const headingId = React.useId()
  const rows = proofRows(state)

  if (compact) {
    return (
      <section aria-labelledby={headingId} data-testid="proof-counter" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-card px-3 py-2">
        <div className="flex items-center gap-3">
          <ShieldCheck aria-hidden className="size-6 shrink-0" />
          <div>
            <h2 id={headingId} className="text-sm font-semibold leading-tight">
              {d("proof.title")}
            </h2>
            <p className="text-xs text-muted-foreground">{d("proof.total")}</p>
          </div>
          <span className="text-4xl font-extrabold tabular-nums leading-none" data-testid="proof-total">
            {state.total}
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{d("proof.empty")}</p>
        ) : (
          <ul aria-label={d("proof.count")} className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {rows.map(({ category, count }) => (
              <li key={category} data-category={category} className="inline-flex items-center gap-1.5 rounded-full border bg-background px-3 py-1 text-sm">
                <span className="font-medium">{t(categoryKey(category))}</span>
                <span className="font-bold tabular-nums">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    )
  }

  return (
    <section aria-labelledby={headingId} data-testid="proof-counter" className="space-y-3 rounded-lg border bg-card p-4">
      <h2 id={headingId} className="flex items-center gap-2 text-lg font-semibold">
        <ShieldCheck aria-hidden className="size-5" />
        {d("proof.title")}
      </h2>
      <p className="flex items-baseline gap-3">
        <span className="text-5xl font-extrabold tabular-nums" data-testid="proof-total">
          {state.total}
        </span>
        <span className="text-sm text-muted-foreground">{d("proof.total")}</span>
      </p>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{d("proof.empty")}</p>
      ) : (
        <ul aria-label={d("proof.count")} className="grid gap-2 sm:grid-cols-2">
          {rows.map(({ category, count }) => (
            <li
              key={category}
              data-category={category}
              className="flex items-center justify-between gap-3 rounded-md border bg-background px-3 py-2"
            >
              <span className="min-w-0 font-medium leading-snug">{t(categoryKey(category))}</span>
              <span className="shrink-0 text-xl font-bold tabular-nums">{count}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{d("proof.note")}</p>
    </section>
  )
}
