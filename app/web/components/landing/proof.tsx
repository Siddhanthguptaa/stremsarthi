"use client"

import Link from "next/link"

import { Button } from "@/components/ui/button"
import { useLanguage } from "@/components/shell/language-provider"
import { lt, type LandingKey } from "@/lib/landing-text"

const POINTS = ["p1", "p2", "p3", "p4"] as const
const EXTRAS = ["e1", "e2", "e3"] as const

export function Proof() {
  const { lang } = useLanguage()
  return (
    <>
      <section aria-labelledby="proof-title" className="mt-16 md:mt-24">
        <h2 id="proof-title" className="max-w-2xl text-4xl font-extrabold leading-[1.05] md:text-5xl">
          {lt(lang, "proof.title")}
        </h2>
        <dl className="mt-8 grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-2 lg:grid-cols-4">
          {POINTS.map((p) => (
            <div key={p} className="bg-card p-5">
              <dt className="text-2xl font-bold leading-tight">{lt(lang, `proof.${p}.title` as LandingKey)}</dt>
              <dd className="mt-2 text-base text-muted-foreground">{lt(lang, `proof.${p}.body` as LandingKey)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="extras-title" className="mt-16 md:mt-24">
        <h2 id="extras-title" className="text-4xl font-extrabold leading-[1.05] md:text-5xl">
          {lt(lang, "extras.title")}
        </h2>
        <div className="mt-8 grid gap-x-8 gap-y-6 md:grid-cols-3">
          {EXTRAS.map((e) => (
            <div key={e} className="border-l-4 border-primary pl-4">
              <h3 className="text-2xl font-bold">{lt(lang, `extras.${e}.title` as LandingKey)}</h3>
              <p className="mt-1 text-base text-muted-foreground">{lt(lang, `extras.${e}.body` as LandingKey)}</p>
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="cta-title" className="module mt-16 flex flex-col gap-6 p-6 md:mt-24 md:flex-row md:items-center md:justify-between md:p-8">
        <div className="max-w-xl">
          <h2 id="cta-title" className="text-4xl font-extrabold leading-[1.05] md:text-5xl">
            {lt(lang, "cta.title")}
          </h2>
          <p className="mt-2 text-base text-muted-foreground">{lt(lang, "cta.body")}</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg" className="h-12 text-base">
            <Link href="/go-live">{lt(lang, "hero.cta")}</Link>
          </Button>
          <Button asChild size="lg" variant="outline" className="h-12 text-base">
            <Link href="/demo">{lt(lang, "hero.demo")}</Link>
          </Button>
        </div>
      </section>
    </>
  )
}
