"use client"

import Link from "next/link"

import { Lanes } from "@/components/landing/lanes"
import { Proof } from "@/components/landing/proof"
import { Reel } from "@/components/landing/reel"
import { SignalPath } from "@/components/landing/signal-path"
import { useLanguage } from "@/components/shell/language-provider"
import { Button } from "@/components/ui/button"
import { lt } from "@/lib/landing-text"

/**
 * Layout choice: on xl the headline sits left and the reel right, so the first screen shows both the
 * promise and the proof. Below xl the headline and CTAs come first, then the reel at full width.
 */
export function LandingPage() {
  const { lang } = useLanguage()
  return (
    <div>
      <div className="grid items-start gap-8 xl:grid-cols-[minmax(0,4fr)_minmax(0,8fr)] xl:gap-10">
        <header className="xl:sticky xl:top-8 xl:pt-6">
          <h1 className="text-[4.25rem] font-extrabold leading-[0.9] xs:text-[5rem] md:text-[7rem] xl:text-[5.5rem] 2xl:text-[6.5rem]">
            <span className="block">{lt(lang, "hero.line1")}</span>
            <span className="block text-primary">{lt(lang, "hero.line2")}</span>
          </h1>
          <div aria-hidden className="mt-4 flex h-3 w-40 gap-1">
            <span className="mosaic mosaic-video flex-1 rounded-sm" />
            <span className="mosaic mosaic-audio flex-1 rounded-sm" />
            <span className="mosaic mosaic-chat flex-1 rounded-sm" />
          </div>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-foreground/90">{lt(lang, "hero.sub")}</p>
          <div className="mt-7 flex flex-col gap-3 sm:flex-row xl:flex-col 2xl:flex-row">
            <Button asChild size="lg" className="h-14 px-8 text-lg">
              <Link href="/go-live">{lt(lang, "hero.cta")}</Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="h-14 px-8 text-lg">
              <Link href="/demo">{lt(lang, "hero.demo")}</Link>
            </Button>
          </div>
          <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
            <span aria-hidden className="led text-success" />
            {lt(lang, "hero.note")}
          </p>
        </header>
        <Reel />
      </div>
      <SignalPath />
      <Lanes />
      <Proof />
    </div>
  )
}
