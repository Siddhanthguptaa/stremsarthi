"use client"

import { Check, Clock, Trash2, X } from "lucide-react"

import type { I18nKey } from "@/lib/i18n"
import { type ModActionKind, type QueueEntry, type TimeoutPreset } from "@/lib/mod-state"
import { useLanguage } from "@/components/shell/language-provider"
import { Button } from "@/components/ui/button"

interface ActionBarProps {
  entry: QueueEntry
  preset: TimeoutPreset
  onAct: (entry: QueueEntry, action: ModActionKind) => void
}

const btn = "h-auto min-h-11 justify-center gap-1.5 md:justify-between whitespace-normal px-2.5 text-left"
const kbd = "keycap ml-auto hidden rounded-sm bg-black/25 px-1.5 font-mono text-[11px] leading-5 md:inline"

/** Approve / delete / timeout / dismiss for one message, as four keys. Staff messages can only be approved or dismissed. */
export function ActionBar({ entry, preset, onAct }: ActionBarProps) {
  const { t } = useLanguage()
  const working = entry.busy !== null
  const removeBlocked = entry.staff
  const length = t(`mod.t.${preset}` as I18nKey)
  return (
    <div role="group" aria-label={t("mod.item").replace("{author}", entry.author)} className="grid grid-cols-2 gap-2 md:w-[17rem] md:shrink-0">
      <Button type="button" className={btn} aria-keyshortcuts="A" onClick={() => onAct(entry, "approve")} disabled={working}>
        <Check aria-hidden />
        {t("mod.approve")}
        <kbd aria-hidden className={kbd}>A</kbd>
      </Button>
      <Button type="button" variant="destructive" className={btn} aria-keyshortcuts="D" onClick={() => onAct(entry, "delete")} disabled={working || removeBlocked}>
        <Trash2 aria-hidden />
        {t("mod.delete")}
        <kbd aria-hidden className={kbd}>D</kbd>
      </Button>
      <Button type="button" variant="outline" className={btn} aria-keyshortcuts="T" onClick={() => onAct(entry, "timeout")} disabled={working || removeBlocked}>
        <Clock aria-hidden />
        {t("mod.timeout").replace("{length}", length)}
        <kbd aria-hidden className={kbd}>T</kbd>
      </Button>
      <Button type="button" variant="secondary" className={btn} aria-keyshortcuts="X" onClick={() => onAct(entry, "dismiss")} disabled={working}>
        <X aria-hidden />
        {t("mod.dismiss")}
        <kbd aria-hidden className={kbd}>X</kbd>
      </Button>
    </div>
  )
}
