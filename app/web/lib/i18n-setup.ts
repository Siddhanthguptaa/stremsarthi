/** Words for the redesigned setup screens (Go live, Controls, Faces), English and Hindi. */

import type { Lang } from "@/lib/i18n"

const TEXT = {
  "setup.path.label": { en: "Signal path", hi: "सिग्नल का रास्ता" },
  "setup.path.source": { en: "Your camera", hi: "आपका कैमरा" },
  "setup.path.sourceSub": { en: "OBS or phone app", hi: "OBS या फ़ोन ऐप" },
  "setup.path.sourceOff": { en: "Not sending yet", hi: "अभी कुछ नहीं भेज रहा" },
  "setup.path.sourceWait": { en: "Waiting for video", hi: "वीडियो का इंतज़ार" },
  "setup.path.sourceOn": { en: "Video is arriving", hi: "वीडियो आ रहा है" },
  "setup.path.engine": { en: "Streamify", hi: "स्ट्रीमिफ़ाई" },
  "setup.path.engineSub": { en: "Delay and masks", hi: "देरी और मास्क" },
  "setup.path.engineOff": { en: "Ready when you start", hi: "शुरू करते ही तैयार" },
  "setup.path.engineOn": { en: "Protecting the stream", hi: "स्ट्रीम की सुरक्षा चालू" },
  "setup.path.engineBad": { en: "Needs attention", hi: "ध्यान चाहिए" },
  "setup.path.dest": { en: "Destinations", hi: "गंतव्य" },
  "setup.path.destNone": { en: "None switched on", hi: "कोई चालू नहीं" },
  "setup.path.destMissing": { en: "A key is missing", hi: "एक कुंजी कम है" },
  "setup.path.destReady": { en: "Keys set", hi: "कुंजियाँ तैयार" },
  "setup.path.destLive": { en: "On air", hi: "ऑन एयर" },
  "setup.step.1": { en: "Choose where to stream", hi: "चुनें कहाँ स्ट्रीम करना है" },
  "setup.step.2": { en: "Set the delay", hi: "देरी तय करें" },
  "setup.step.3": { en: "Check, then go live", hi: "जाँचें, फिर लाइव जाएँ" },
  "setup.checklist": { en: "Pre-flight checklist", hi: "उड़ान से पहले की जाँच" },
  "setup.ingest.step": { en: "Point your camera here", hi: "अपना कैमरा यहाँ भेजें" },
  "setup.controls.video": { en: "What the camera sees", hi: "कैमरा क्या देखता है" },
  "setup.controls.audio": { en: "What people say", hi: "लोग क्या बोलते हैं" },
  "setup.controls.count": { en: "{on} of {all} on", hi: "{all} में से {on} चालू" },
  "setup.controls.details": { en: "Mask settings", hi: "मास्क सेटिंग" },
  "setup.enrol.stage": { en: "Camera", hi: "कैमरा" },
  "setup.enrol.stageIdle": { en: "Camera is off", hi: "कैमरा बंद है" },
  "setup.enrol.slots": { en: "{n} free", hi: "{n} जगह खाली" },
  "setup.enrol.step1": { en: "Name the person", hi: "नाम लिखें" },
  "setup.enrol.step2": { en: "Scan their face", hi: "चेहरा स्कैन करें" },
} as const

export type SetupTextKey = keyof typeof TEXT

export function setupText(lang: Lang, key: SetupTextKey, vars?: Record<string, string | number>): string {
  let out: string = TEXT[key][lang === "hi" ? "hi" : "en"]
  for (const [k, v] of Object.entries(vars ?? {})) out = out.replace(`{${k}}`, String(v))
  return out
}
