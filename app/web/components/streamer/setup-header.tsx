import * as React from "react"

/** Page header for the setup screens: big title, one muted line, optional status on the right. */
export function SetupHeader({
  id,
  title,
  description,
  children,
}: {
  id: string
  title: string
  description: string
  children?: React.ReactNode
}) {
  return (
    <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div className="space-y-2">
        <h1 id={id} className="font-display text-4xl font-extrabold leading-[1.05] md:text-5xl">
          {title}
        </h1>
        <p className="max-w-prose text-muted-foreground">{description}</p>
      </div>
      {children ? <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">{children}</div> : null}
    </header>
  )
}

/** An LED with its text label (colour is never the only signal). */
export function StatusLed({ tone, children }: { tone: "success" | "warning" | "tally" | "muted"; children: React.ReactNode }) {
  const color = { success: "text-success", warning: "text-warning", tally: "text-tally", muted: "text-muted-foreground" }[tone]
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className={`led ${color}`} />
      <span>{children}</span>
    </span>
  )
}
