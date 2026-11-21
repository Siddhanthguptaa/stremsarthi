/**
 * Chat shield status as words a streamer or moderator can act on (EN + HI).
 *
 * The chat process reports fixed codes only: as `safety.event` frames with category `system_status`
 * (source `chat`, `chat.youtube` or `chat.twitch`, the code in `detail_code`) and as the code list of
 * `GET /api/session/health`. Nothing here ever shows a message, a name or a token.
 */

import type { Lang } from "@/lib/i18n"

type Text = { en: string; hi: string }

export const CHAT_STATUS_TEXT: Record<string, Text> = {
  yt_login_required: {
    en: "Chat unavailable: log in to YouTube (run the one-time chat login, see docs/GO_LIVE.md).",
    hi: "चैट बंद है: YouTube में लॉग इन करें (एक बार वाला चैट लॉगिन चलाएँ, docs/GO_LIVE.md देखें)।",
  },
  twitch_login_required: {
    en: "Chat unavailable: log in to Twitch (run the one-time chat login, see docs/GO_LIVE.md).",
    hi: "चैट बंद है: Twitch में लॉग इन करें (एक बार वाला चैट लॉगिन चलाएँ, docs/GO_LIVE.md देखें)।",
  },
  twitch_auth_failed: {
    en: "Twitch chat login failed or expired: log in to Twitch again.",
    hi: "Twitch चैट लॉगिन नहीं हुआ या पुराना हो गया: Twitch में फिर से लॉग इन करें।",
  },
  yt_no_credentials: {
    en: "YouTube chat is off: the YouTube OAuth keys are missing in .env.",
    hi: "YouTube चैट बंद है: .env में YouTube OAuth की कुंजियाँ नहीं हैं।",
  },
  twitch_no_credentials: {
    en: "Twitch chat is off: TWITCH_CLIENT_ID is missing in .env.",
    hi: "Twitch चैट बंद है: .env में TWITCH_CLIENT_ID नहीं है।",
  },
  twitch_no_channel: {
    en: "Twitch chat is off: no channel is set.",
    hi: "Twitch चैट बंद है: चैनल सेट नहीं है।",
  },
  chat_unavailable: {
    en: "Chat unavailable: the platform chat could not be reached. Nothing is being filtered, watch your chat yourself.",
    hi: "चैट उपलब्ध नहीं: प्लेटफ़ॉर्म की चैट से जुड़ नहीं पाए। कुछ भी फ़िल्टर नहीं हो रहा, चैट ख़ुद देखें।",
  },
  chat_connectors_failed: {
    en: "Chat shield stopped: no chat connection is working. Nothing is being filtered, watch your chat yourself.",
    hi: "चैट शील्ड रुक गई: कोई चैट कनेक्शन नहीं चल रहा। कुछ भी फ़िल्टर नहीं हो रहा, चैट ख़ुद देखें।",
  },
  groq_key_missing: {
    en: "Chat checks use basic rules only (no Groq key): expect more messages held for the moderator.",
    hi: "चैट जाँच सिर्फ़ बुनियादी नियमों से हो रही है (Groq कुंजी नहीं): मॉडरेटर के पास ज़्यादा संदेश रुकेंगे।",
  },
  // Process-level reasons from `GET /api/session/health` when the chat process itself is down.
  no_connector: {
    en: "Chat unavailable: no YouTube or Twitch chat is set up (log in once, see docs/GO_LIVE.md).",
    hi: "चैट बंद है: YouTube या Twitch की चैट सेट नहीं है (एक बार लॉग इन करें, docs/GO_LIVE.md देखें)।",
  },
  connectors_failed: {
    en: "Chat shield stopped: no chat connection is working. Nothing is being filtered.",
    hi: "चैट शील्ड रुक गई: कोई चैट कनेक्शन नहीं चल रहा। कुछ भी फ़िल्टर नहीं हो रहा।",
  },
  chat_failed: {
    en: "Chat shield stopped unexpectedly. Nothing is being filtered, watch your chat yourself.",
    hi: "चैट शील्ड अचानक रुक गई। कुछ भी फ़िल्टर नहीं हो रहा, चैट ख़ुद देखें।",
  },
}

export function chatStatusText(code: string, lang: Lang): string | null {
  const entry = CHAT_STATUS_TEXT[code]
  return entry ? entry[lang === "hi" ? "hi" : "en"] : null
}

/** The code of a chat `system_status` safety event, or null for any other event. */
export function chatStatusCode(event: { category: string; source: string; detail_code: string }): string | null {
  if (event.category !== "system_status" || !(event.source === "chat" || event.source.startsWith("chat."))) return null
  return event.detail_code in CHAT_STATUS_TEXT ? event.detail_code : null
}

/** Adds a code once, keeping the order they arrived in. */
export function withCode(codes: readonly string[], code: string): string[] {
  return codes.includes(code) ? [...codes] : [...codes, code]
}

/** Codes to show from `GET /api/session/health`. Empty while chat is off on purpose or running without problems. */
export function chatProblemsFromHealth(chat: { enabled: boolean; process: string; reason: string | null; status_codes: string[] } | null | undefined): string[] {
  if (!chat || !chat.enabled) return []
  const codes = chat.status_codes.filter((c) => c in CHAT_STATUS_TEXT)
  if (chat.process === "failed" || chat.process === "exited") {
    if (chat.reason && chat.reason in CHAT_STATUS_TEXT) codes.push(chat.reason)
    else if (codes.length === 0) codes.push("chat_failed")
  }
  return [...new Set(codes)]
}
