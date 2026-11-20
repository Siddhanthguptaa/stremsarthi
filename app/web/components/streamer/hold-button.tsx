"use client"

import { Loader2, OctagonPause, Play, TriangleAlert } from "lucide-react"

import { holdSourceKey } from "@/lib/format"
import { type HoldView, intentForPress } from "@/lib/hold-machine"
import { translate } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface HoldButtonProps {
  view: HoldView
  /** The engine's own status agrees with the confirmed state (it reports hold_active). */
  engineConfirmed: boolean
  onPress: () => void
  confirmOpen: boolean
  onConfirmOpenChange: (open: boolean) => void
  onConfirmResume: () => void
  className?: string
}

/**
 * The HOLD button (docs/specs/app-web.md 5.2). One tap holds at once. Resuming asks for confirmation.
 * State is shown by text, icon and colour. The label is bilingual so it can be read under stress.
 */
export function HoldButton({
  view,
  engineConfirmed,
  onPress,
  confirmOpen,
  onConfirmOpenChange,
  onConfirmResume,
  className,
}: HoldButtonProps) {
  const { t, lang } = useLanguage()
  const other = lang === "en" ? "hi" : "en"
  const both = (key: Parameters<typeof t>[0]) => (
    <>
      <span>{t(key)}</span>
      <span lang={other} className="block text-base font-semibold opacity-90">
        {translate(other, key)}
      </span>
    </>
  )

  const disabled = intentForPress(view) === null
  let icon = <OctagonPause aria-hidden className="size-10 shrink-0" />
  let label = both("action.hold")
  let hint: string | null = t("hold.keyHint")
  let tone = "bg-destructive text-destructive-foreground hover:bg-destructive/90"

  switch (view.phase) {
    case "unknown":
    case "released":
      break
    case "sending":
      icon = <Loader2 aria-hidden className="size-10 shrink-0 animate-spin" />
      label = both("hold.sending")
      hint = null
      tone = "bg-destructive text-destructive-foreground"
      break
    case "resuming":
      icon = <Loader2 aria-hidden className="size-10 shrink-0 animate-spin" />
      label = both("hold.resuming")
      hint = null
      tone = "bg-warning text-warning-foreground"
      break
    case "holding":
      icon = view.autoOnly ? <OctagonPause aria-hidden className="size-10 shrink-0" /> : <Play aria-hidden className="size-10 shrink-0" />
      label = both("hold.holding")
      hint = view.autoOnly ? t("hold.autoOnly") : t("hold.tapResume")
      tone = "bg-warning text-warning-foreground hover:bg-warning/90"
      break
    case "failed":
      icon = <TriangleAlert aria-hidden className="size-10 shrink-0" />
      label = both("hold.retry")
      hint = t("hold.failed")
      tone = "bg-destructive text-destructive-foreground ring-4 ring-warning hover:bg-destructive/90"
      break
  }

  const heldBy = view.held && view.sources.length > 0 ? view.sources.map((s) => t(holdSourceKey(s))).join(", ") : null
  const secondary = [
    hint,
    heldBy ? `${t("hold.heldBy")}: ${heldBy}` : null,
    view.unconfirmed ? t("hold.unconfirmed") : view.held && engineConfirmed ? t("hold.engineConfirmed") : null,
  ].filter(Boolean)

  return (
    <div className={cn("space-y-2", className)}>
      <Button
        type="button"
        onClick={disabled ? undefined : onPress}
        aria-pressed={view.held}
        aria-disabled={disabled}
        data-phase={view.phase}
        className={cn(
          "h-auto min-h-24 w-full flex-col gap-1 whitespace-normal px-4 py-3 text-3xl font-extrabold leading-tight shadow-lg",
          disabled && "cursor-not-allowed",
          tone
        )}
      >
        <span className="flex items-center justify-center gap-3 text-center">
          {icon}
          <span>{label}</span>
        </span>
      </Button>
      {secondary.length > 0 ? (
        <p className="text-center text-sm text-muted-foreground" data-testid="hold-hint">
          {secondary.join(" · ")}
        </p>
      ) : null}
      {/* Announced assertively on every change (docs/specs/app-web.md 5.6). */}
      <p className="sr-only" role="status" aria-live="assertive" aria-atomic="true">
        {view.held ? t("hold.holdingTitle") : view.phase === "released" ? t("hold.liveTitle") : ""}
        {view.phase === "failed" ? t("hold.failed") : ""}
      </p>

      <Dialog open={confirmOpen} onOpenChange={onConfirmOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("resume.title")}
              <span lang={other} className="block text-base font-medium text-muted-foreground">
                {translate(other, "resume.title")}
              </span>
            </DialogTitle>
            <DialogDescription>{t("resume.body")}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" className="min-h-12" onClick={() => onConfirmOpenChange(false)}>
              {t("resume.cancel")}
            </Button>
            <Button type="button" className="min-h-12" onClick={onConfirmResume} autoFocus={false}>
              {t("resume.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
