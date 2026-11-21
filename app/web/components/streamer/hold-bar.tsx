"use client"

import { useLanguage } from "@/components/shell/language-provider"
import { HoldButton } from "@/components/streamer/hold-button"
import { useHold } from "@/components/streamer/use-hold"

/** The HOLD button pinned to the bottom of a screen (used on /controls; /live has its own). */
export function HoldBar() {
  const { t } = useLanguage()
  const h = useHold()
  return (
    <div
      role="region"
      aria-label={t("hold.region")}
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 px-4 pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80"
      style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
    >
      <div className="mx-auto w-full max-w-5xl">
        <HoldButton
          view={h.view}
          engineConfirmed={h.engineConfirmed}
          onPress={h.press}
          confirmOpen={h.confirmOpen}
          onConfirmOpenChange={h.setConfirmOpen}
          onConfirmResume={h.confirmResume}
        />
      </div>
    </div>
  )
}
