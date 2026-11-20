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

const btn = "h-auto min-h-11 whitespace-normal text-center"

/** Approve / delete / timeout / dismiss for one message. Staff messages can only be approved or dismissed. */
export function ActionBar({ entry, preset, onAct }: ActionBarProps) {
  const { t } = useLanguage()
  const working = entry.busy !== null
  const removeBlocked = entry.staff
  const length = t(`mod.t.${preset}` as I18nKey)
  return (
    <div role="group" aria-label={t("mod.item").replace("{author}", entry.author)} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Button type="button" className={btn} onClick={() => onAct(entry, "approve")} disabled={working}>
        <Check aria-hidden />
        {t("mod.approve")}
      </Button>
      <Button type="button" variant="destructive" className={btn} onClick={() => onAct(entry, "delete")} disabled={working || removeBlocked}>
        <Trash2 aria-hidden />
        {t("mod.delete")}
      </Button>
      <Button type="button" variant="outline" className={btn} onClick={() => onAct(entry, "timeout")} disabled={working || removeBlocked}>
        <Clock aria-hidden />
        {t("mod.timeout").replace("{length}", length)}
      </Button>
      <Button type="button" variant="secondary" className={btn} onClick={() => onAct(entry, "dismiss")} disabled={working}>
        <X aria-hidden />
        {t("mod.dismiss")}
      </Button>
    </div>
  )
}
