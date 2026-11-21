/** Words for the go-live output status, destination keys and the LIVE banner (EN + HI). */

import type { Lang } from "@/lib/i18n"

const TEXT = {
  "live.banner.live": { en: "LIVE to {names}", hi: "{names} पर लाइव" },
  "live.banner.liveSub": {
    en: "Viewers are watching the protected stream over RTMPS.",
    hi: "दर्शक RTMPS पर सुरक्षित स्ट्रीम देख रहे हैं।",
  },
  "live.banner.rehearsal": { en: "REHEARSAL: not live", hi: "रिहर्सल: लाइव नहीं" },
  "live.banner.rehearsalSub": {
    en: "Nothing is sent to YouTube or Twitch. Use Go live to start a real stream.",
    hi: "YouTube या Twitch पर कुछ नहीं जा रहा। असली स्ट्रीम के लिए Go live दबाएँ।",
  },
  "live.banner.waiting": { en: "Starting: not live yet", hi: "शुरू हो रहा है: अभी लाइव नहीं" },
  "live.banner.waitingSub": {
    en: "Waiting for your camera app to publish to the address on the Go live page.",
    hi: "आपके कैमरा ऐप के Go live पेज वाले पते पर भेजने का इंतज़ार है।",
  },
  "live.banner.none": { en: "No stream running", hi: "कोई स्ट्रीम नहीं चल रही" },
  "live.banner.noneSub": { en: "Open Go live to start.", hi: "शुरू करने के लिए Go live खोलें।" },
  "golive.out.rtmps": { en: "Sends to {name} over RTMPS", hi: "{name} को RTMPS से भेजेगा" },
  "golive.out.previewOnly": {
    en: "Preview only: nothing leaves this PC",
    hi: "सिर्फ़ प्रीव्यू: इस PC से कुछ बाहर नहीं जाता",
  },
  "golive.out.liveNow": { en: "LIVE now over RTMPS", hi: "अभी RTMPS पर लाइव" },
  "golive.out.rehearsalNow": { en: "Rehearsal: preview only", hi: "रिहर्सल: सिर्फ़ प्रीव्यू" },
  "golive.keyFrom.env": { en: "key found in .env", hi: ".env में कुंजी मिली" },
  "golive.keyFrom.session": { en: "key typed here (kept in memory only)", hi: "यहाँ टाइप की कुंजी (सिर्फ़ मेमोरी में)" },
  "golive.keyNone": { en: "No key yet", hi: "अभी कुंजी नहीं" },
  "golive.key.label": { en: "Stream key for {name}", hi: "{name} की स्ट्रीम कुंजी" },
  "golive.key.set": { en: "Save key", hi: "कुंजी सहेजें" },
  "golive.key.clear": { en: "Forget key", hi: "कुंजी भूलें" },
  "golive.key.saved": { en: "Key saved for this run. It is never shown again.", hi: "इस बार के लिए कुंजी सहेजी। यह दोबारा नहीं दिखेगी।" },
  "golive.key.bad": { en: "That does not look like a stream key.", hi: "यह स्ट्रीम कुंजी जैसी नहीं लगती।" },
  "golive.key.locked": { en: "Stop the session before changing a key.", hi: "कुंजी बदलने से पहले सेशन रोकें।" },
  "golive.key.hint": {
    en: "Paste it here or put it in .env. It stays in memory, is never displayed and is lost when the app stops.",
    hi: "यहाँ चिपकाएँ या .env में रखें। यह सिर्फ़ मेमोरी में रहती है, कभी दिखती नहीं और ऐप बंद होने पर मिट जाती है।",
  },
  "golive.switch": { en: "Go live now (stops the rehearsal)", hi: "अभी लाइव जाएँ (रिहर्सल रुकेगी)" },
  "golive.switching": { en: "Switching to live...", hi: "लाइव पर जा रहे हैं..." },
  "golive.rehearsalRunning": {
    en: "A rehearsal is running. Pick a platform above, then switch to live.",
    hi: "रिहर्सल चल रही है। ऊपर प्लेटफ़ॉर्म चुनें, फिर लाइव पर जाएँ।",
  },
  "golive.newAddress": {
    en: "New publish address: the login changed. Copy the address below into OBS or your phone app again before you start publishing.",
    hi: "नया पब्लिश पता: लॉगिन बदल गया है। पब्लिश करने से पहले नीचे का पता OBS या फ़ोन ऐप में फिर से डालें।",
  },
  "golive.stableLogin": {
    en: "Your publish login is fixed (set in .env): the OBS or phone setup is one-time.",
    hi: "आपका पब्लिश लॉगिन तय है (.env में): OBS या फ़ोन सेटअप सिर्फ़ एक बार का है।",
  },
  "golive.err.stopping": {
    en: "The rehearsal is still stopping. Try again in a few seconds.",
    hi: "रिहर्सल अभी रुक रही है। कुछ सेकंड बाद फिर कोशिश करें।",
  },
} as const satisfies Record<string, { en: string; hi: string }>

export type GoLiveTextKey = keyof typeof TEXT

export function glText(lang: Lang, key: GoLiveTextKey, vars?: Record<string, string>): string {
  let out: string = TEXT[key][lang === "hi" ? "hi" : "en"]
  for (const [k, v] of Object.entries(vars ?? {})) out = out.replace(`{${k}}`, v)
  return out
}

export const PLATFORM_NAME = { youtube: "YouTube", twitch: "Twitch" } as const

export type BannerKind = "live" | "waiting" | "rehearsal" | "none"

/** What /live shows in the big banner. "live" only when the engine says live AND the session goes to a platform. */
export function bannerKind(session: { state: string; output?: "live" | "rehearsal" | null } | null): BannerKind {
  if (!session || !session.output) return "none"
  if (session.state === "live") return session.output === "live" ? "live" : "rehearsal"
  if (session.state === "starting" || session.state === "waiting_ingest") return "waiting"
  if (session.state === "stopping" || session.state === "engine_lost" || session.state === "failed") return "none"
  return "none"
}
