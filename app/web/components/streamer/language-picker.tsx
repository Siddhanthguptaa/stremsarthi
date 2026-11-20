"use client"

import * as React from "react"
import { Loader2 } from "lucide-react"

import { toggleLanguage } from "@/lib/controls"
import { en, type I18nKey } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"

interface LanguagePickerProps {
  /** Language codes being checked (`audio.languages`). */
  selected: readonly string[]
  /** Codes that can be chosen, in display order. */
  supported: readonly string[]
  /** A change is on its way to the API: the switches wait for the answer. */
  saving?: boolean
  onChange: (languages: string[]) => void
}

const labelKey = (code: string): I18nKey | null => {
  const key = `speech.lang.${code}`
  return key in en ? (key as I18nKey) : null
}

/** Which spoken languages Stream Sarthi listens in. At least one stays on. */
export function LanguagePicker({ selected, supported, saving = false, onChange }: LanguagePickerProps) {
  const { t } = useLanguage()
  const id = React.useId()
  const onlyOne = selected.length <= 1
  return (
    <fieldset className="space-y-3 rounded-lg border bg-card p-4" data-testid="speech-languages">
      <legend className="flex items-center gap-2 px-1 text-base font-semibold">
        {t("speech.languages")}
        {saving ? <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" /> : null}
      </legend>
      <p className="text-sm text-muted-foreground">{t("speech.languages.desc")}</p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {supported.map((code) => {
          const on = selected.includes(code)
          const key = labelKey(code)
          const lastOne = on && onlyOne
          return (
            <li
              key={code}
              className="flex min-h-11 items-center justify-between gap-3 rounded-md border bg-background px-3 py-2"
            >
              <Label htmlFor={`${id}-${code}`} className="text-base">
                {key ? t(key) : code}
              </Label>
              <Switch
                id={`${id}-${code}`}
                checked={on}
                disabled={saving || lastOne}
                aria-describedby={lastOne ? `${id}-last` : undefined}
                onCheckedChange={(next) => {
                  const languages = toggleLanguage(selected, code, next, supported)
                  if (languages) onChange(languages)
                }}
              />
            </li>
          )
        })}
      </ul>
      {onlyOne ? (
        <p id={`${id}-last`} className="text-xs text-muted-foreground">
          {t("speech.lang.last")}
        </p>
      ) : null}
    </fieldset>
  )
}
