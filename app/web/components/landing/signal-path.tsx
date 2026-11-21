"use client"

import { useLanguage } from "@/components/shell/language-provider"
import { lt, type LandingKey } from "@/lib/landing-text"
import { cn } from "@/lib/utils"

/** The delay window drawn as a strip of cells: masks land in it, the last cell is "on air". */
const TAPE: (string | null)[] = ["video", null, "audio", "video", "chat", null, "video", null]

function DelayTape({ label }: { label: string }) {
  return (
    <div role="img" aria-label={label} className="mt-4 flex items-center gap-1">
      {TAPE.map((m, i) => (
        <span
          key={i}
          className={cn(
            "h-7 flex-1 rounded-sm",
            m ? cn("mosaic", `mosaic-${m}`) : "border bg-background/60",
          )}
        />
      ))}
      <span className="led ml-1.5 text-tally" />
    </div>
  )
}

function Jack({ className }: { className?: string }) {
  return <span aria-hidden className={cn("led text-success", className)} />
}

function Cable({ label }: { label: string }) {
  return (
    <div
      aria-hidden
      className="relative flex h-16 items-center justify-center lg:h-auto lg:w-28"
    >
      <div className="flex flex-col items-center lg:flex-row">
        <Jack />
        <span className="h-10 w-1 rounded-full bg-border lg:h-1 lg:w-20" />
        <Jack />
      </div>
      <span className="absolute left-1/2 top-1/2 ml-4 -translate-y-1/2 whitespace-nowrap text-xs text-muted-foreground lg:bottom-full lg:left-1/2 lg:top-auto lg:mb-2 lg:ml-0 lg:-translate-x-1/2 lg:translate-y-0">
        {label}
      </span>
    </div>
  )
}

function Node({
  n,
  title,
  body,
  children,
  main = false,
}: {
  n: number
  title: string
  body: string
  children?: React.ReactNode
  main?: boolean
}) {
  const { lang } = useLanguage()
  return (
    <div className={cn("module p-4 sm:p-5", main && "border-primary")}>
      <div className="flex items-center gap-2.5">
        <span
          className={cn(
            "flex size-7 items-center justify-center rounded-sm font-display text-lg font-extrabold leading-none",
            main ? "bg-primary text-primary-foreground" : "bg-desk-raised text-foreground",
          )}
        >
          <span className="sr-only">{lt(lang, "path.step")} </span>
          {n}
        </span>
        <h3 className="text-2xl font-bold leading-none">{title}</h3>
      </div>
      <p className="mt-3 text-base text-muted-foreground">{body}</p>
      {children}
    </div>
  )
}

function Note({ k }: { k: "safe" | "hold" }) {
  const { lang } = useLanguage()
  return (
    <div className="module-row flex gap-3 p-4">
      <span aria-hidden className={cn("led mt-1.5", k === "safe" ? "text-success" : "text-warning")} />
      <div>
        <h3 className="text-xl font-bold">{lt(lang, `path.${k}.title` as LandingKey)}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{lt(lang, `path.${k}.body` as LandingKey)}</p>
      </div>
    </div>
  )
}

export function SignalPath() {
  const { lang } = useLanguage()
  return (
    <section aria-labelledby="path-title" className="mt-16 md:mt-24">
      <header className="max-w-2xl">
        <h2 id="path-title" className="text-4xl font-extrabold leading-[1.05] md:text-5xl">
          {lt(lang, "path.title")}
        </h2>
        <p className="mt-2 text-base text-muted-foreground">{lt(lang, "path.desc")}</p>
      </header>

      <div className="mt-8 grid items-stretch lg:grid-cols-[1fr_auto_1.35fr_auto_1fr]">
        <Node n={1} title={lt(lang, "path.n1.title")} body={lt(lang, "path.n1.body")} />
        <Cable label={lt(lang, "path.cable1")} />
        <Node main n={2} title={lt(lang, "path.n2.title")} body={lt(lang, "path.n2.body")}>
          <DelayTape label={lt(lang, "path.n2.tape")} />
        </Node>
        <Cable label={lt(lang, "path.cable2")} />
        <Node n={3} title={lt(lang, "path.n3.title")} body={lt(lang, "path.n3.body")} />
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <Note k="safe" />
        <Note k="hold" />
      </div>
    </section>
  )
}
