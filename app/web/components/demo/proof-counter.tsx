"use client"

import { ShieldCheck } from "lucide-react"

import { categoryKey } from "@/lib/format"
import { type ProofState, proofRows } from "@/lib/demo-proof"
import { useDemoText } from "@/components/demo/use-demo-text"
import { useLanguage } from "@/components/shell/language-provider"

/** The privacy-proof counter: a running total and a per-category count. Categories and numbers only. */
export function ProofCounter({ state }: { state: ProofState }) {
  const { t } = useLanguage()
  const { d } = useDemoText()
  const rows = proofRows(state)

  return (
    <section aria-labelledby="proof-heading" data-testid="proof-counter" className="space-y-3 rounded-lg border bg-card p-4">
      <h2 id="proof-heading" className="flex items-center gap-2 text-lg font-semibold">
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
