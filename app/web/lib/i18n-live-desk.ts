/** Strings for the /live broadcast desk (English and Hindi). */
import type { Lang } from "@/lib/i18n"

const EN = {
  "tape.title": "Delay tape",
  "tape.desc": "Each mask travels from detected to on air. Viewers see it only after the delay.",
  "tape.detected": "Detected",
  "tape.onAir": "On air",
  "tape.empty": "Nothing masked in the last 10 seconds",
  "tape.summary": "Last {window} seconds: {total} masked ({video} on screen, {audio} audio, {chat} chat). On air after {delay} seconds.",
  "tape.mask": "Mask",
  "tape.second": "{n} s",
  "tally.onAir": "ON AIR",
  "tally.holding": "HOLDING",
  "tally.offAir": "OFF AIR",
  "log.title": "Event log",
  "log.time": "Time",
  "desk.tools": "Tools",
} as const

const HI: Record<keyof typeof EN, string> = {
  "tape.title": "डिले टेप",
  "tape.desc": "हर मास्क पकड़े जाने से ऑन एयर तक चलता है। दर्शक इसे देरी के बाद ही देखते हैं।",
  "tape.detected": "पकड़ा गया",
  "tape.onAir": "ऑन एयर",
  "tape.empty": "पिछले 10 सेकंड में कुछ मास्क नहीं हुआ",
  "tape.summary": "पिछले {window} सेकंड: {total} मास्क ({video} स्क्रीन, {audio} ऑडियो, {chat} चैट)। {delay} सेकंड बाद ऑन एयर।",
  "tape.mask": "मास्क",
  "tape.second": "{n} से",
  "tally.onAir": "ऑन एयर",
  "tally.holding": "होल्ड",
  "tally.offAir": "ऑफ एयर",
  "log.title": "इवेंट लॉग",
  "log.time": "समय",
  "desk.tools": "औज़ार",
}

export type DeskKey = keyof typeof EN

export function deskText(lang: Lang, key: DeskKey, vars?: Record<string, string | number>): string {
  let s: string = lang === "hi" ? HI[key] : EN[key]
  for (const [k, v] of Object.entries(vars ?? {})) s = s.replace(`{${k}}`, String(v))
  return s
}
