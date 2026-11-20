"use client"

import * as React from "react"
import { Loader2 } from "lucide-react"

import type { CategorySettings } from "@/lib/api"
import { type CategoryChange, needsDisableConfirm, sensitivityPercent } from "@/lib/controls"
import { en, type I18nKey } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"

const STYLES = ["blur", "pixelate", "solid"] as const
type Style = (typeof STYLES)[number]
const STYLE_KEYS: Record<Style, I18nKey> = {
  blur: "controls.style.blur",
  pixelate: "controls.style.pixelate",
  solid: "controls.style.solid",
}

/** The translated name of a category, or its code for one this app does not know yet. */
export function useCategoryLabel(): (category: string) => string {
  const { t } = useLanguage()
  return React.useCallback(
    (category) => {
      const key = `category.${category}`
      return key in en ? t(key as I18nKey) : category
    },
    [t],
  )
}

interface CategoryToggleProps {
  category: string
  settings: CategorySettings
  /** A change is on its way to the API. */
  saving?: boolean
  onChange: (category: string, change: CategoryChange) => void
}

/** One card per category: enable switch, mask style, sensitivity slider, "mask when unsure". */
export function CategoryToggle({ category, settings, saving = false, onChange }: CategoryToggleProps) {
  const { t } = useLanguage()
  const label = useCategoryLabel()(category)
  const id = React.useId()
  const [confirmOpen, setConfirmOpen] = React.useState(false)
  const [dragging, setDragging] = React.useState<number | null>(null)

  const shownSensitivity = dragging ?? settings.sensitivity
  const off = !settings.enabled

  const setEnabled = (enabled: boolean) => {
    const change: CategoryChange = { enabled }
    if (needsDisableConfirm(category, change)) setConfirmOpen(true)
    else onChange(category, change)
  }

  return (
    <Card data-category={category} data-enabled={settings.enabled}>
      <CardHeader className="flex-row items-center justify-between gap-3 space-y-0 pb-3">
        <CardTitle className="text-base leading-snug">
          <Label htmlFor={`${id}-enabled`} className="text-base font-semibold">
            {label}
          </Label>
        </CardTitle>
        <div className="flex shrink-0 items-center gap-2">
          {saving ? <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" /> : null}
          <span className="text-sm text-muted-foreground" aria-hidden>
            {settings.enabled ? t("controls.on") : t("controls.off")}
          </span>
          <Switch
            id={`${id}-enabled`}
            checked={settings.enabled}
            onCheckedChange={setEnabled}
            aria-label={`${t("controls.protect")}: ${label}`}
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor={`${id}-style`}>{t("controls.style")}</Label>
          <Select
            id={`${id}-style`}
            value={settings.style}
            disabled={off}
            onChange={(e) => onChange(category, { style: e.target.value as Style })}
          >
            {STYLES.map((style) => (
              <option key={style} value={style}>
                {t(STYLE_KEYS[style])}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <Label id={`${id}-sens`}>{t("controls.sensitivity")}</Label>
            <span className="text-sm tabular-nums text-muted-foreground">{sensitivityPercent(shownSensitivity)} %</span>
          </div>
          <Slider
            thumbLabel={`${t("controls.sensitivity")}: ${label}`}
            valueText={`${sensitivityPercent(shownSensitivity)} %`}
            min={0}
            max={1}
            step={0.05}
            value={[shownSensitivity]}
            disabled={off}
            onValueChange={([v]) => setDragging(v)}
            onValueCommit={([v]) => {
              setDragging(null)
              if (v !== settings.sensitivity) onChange(category, { sensitivity: v })
            }}
          />
          <p className="text-xs text-muted-foreground">{t("controls.sensitivity.hint")}</p>
        </div>

        <div className="flex items-center justify-between gap-3">
          <Label htmlFor={`${id}-unsure`}>{t("controls.unsure")}</Label>
          <Switch
            id={`${id}-unsure`}
            checked={settings.mask_when_unsure}
            disabled={off}
            onCheckedChange={(v) => onChange(category, { mask_when_unsure: v })}
          />
        </div>
      </CardContent>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("controls.confirm.title")} {label}
            </DialogTitle>
            <DialogDescription>{t("controls.confirm.body")}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" size="lg" onClick={() => setConfirmOpen(false)}>
              {t("controls.confirm.no")}
            </Button>
            <Button
              variant="destructive"
              size="lg"
              onClick={() => {
                setConfirmOpen(false)
                onChange(category, { enabled: false })
              }}
            >
              {t("controls.confirm.yes")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
