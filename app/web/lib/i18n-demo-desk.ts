/** Strings for the broadcast-desk layout of the demo console and the safety report (English first, Hindi alongside). */

import type { Lang } from "@/lib/i18n"

export const deskEn = {
  "tally.onair": "ON AIR",
  "tally.hold": "HOLD",
  "clock.label": "Clock",
  "monitor.program": "Program",
  "monitor.source": "Source",
  "proof.video": "Video",
  "proof.audio": "Audio",
  "proof.chat": "Chat",
  "proof.byModality": "Protected events by source",
  "risk.level": "Risk level",
  "report.strip": "Whole session",
  "report.stripHint": "Each tick is one protected event. Red bands are holds.",
  "report.sessionId": "Session",
  "report.totalsBySource": "Protected events by source",
} as const

export type DeskKey = keyof typeof deskEn

export const deskHi: Record<DeskKey, string> = {
  "tally.onair": "ऑन एयर",
  "tally.hold": "होल्ड",
  "clock.label": "घड़ी",
  "monitor.program": "प्रोग्राम",
  "monitor.source": "सोर्स",
  "proof.video": "वीडियो",
  "proof.audio": "ऑडियो",
  "proof.chat": "चैट",
  "proof.byModality": "स्रोत के अनुसार सुरक्षित घटनाएँ",
  "risk.level": "जोखिम स्तर",
  "report.strip": "पूरा सेशन",
  "report.stripHint": "हर निशान एक सुरक्षित घटना है। लाल पट्टियाँ होल्ड हैं।",
  "report.sessionId": "सेशन",
  "report.totalsBySource": "स्रोत के अनुसार सुरक्षित घटनाएँ",
}

export function deskText(lang: Lang, key: DeskKey): string {
  return (lang === "hi" ? deskHi : deskEn)[key]
}
