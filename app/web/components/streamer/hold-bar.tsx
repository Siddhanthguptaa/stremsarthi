"use client"

import type { ReactNode } from "react"

import { useLanguage } from "@/components/shell/language-provider"
import { HoldButton } from "@/components/streamer/hold-button"
import { useHold } from "@/components/streamer/use-hold"

/**
 * The fixed dock the HOLD key sits in. It rests on the phone tab bar (`--bottom-nav-h`) and starts after the
 * desktop rail (`md:left-60`), so it never covers either.
 */
export function HoldDock({ children }: { children: ReactNode }) {
  const { t } = useLanguage()
  return (
    <div
      role="region"
      aria-label={t("hold.region")}
      className="fixed inset-x-0 z-40 border-t bg-background px-4 pt-2 md:left-60"
      style={{ bottom: "var(--bottom-nav-h)", paddingBottom: "max(0.5rem, env(safe-area-inset-bottom))" }}
    >
      <div className="mx-auto w-full max-w-5xl">{children}</div>
    </div>
  )
}

/** The HOLD button pinned to the bottom of a screen (used on /controls; /live uses the dock directly). */
export function HoldBar() {
  const h = useHold()
  return (
    <HoldDock>
      <HoldButton
        view={h.view}
        engineConfirmed={h.engineConfirmed}
        onPress={h.press}
        confirmOpen={h.confirmOpen}
        onConfirmOpenChange={h.setConfirmOpen}
        onConfirmResume={h.confirmResume}
      />
    </HoldDock>
  )
}
