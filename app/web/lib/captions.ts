/**
 * Captions on/off and language (`captions.enabled`, `captions.target_lang` through `PATCH /api/config`), and the
 * transient caption lines shown on the live screen. Caption text is the streamer's own speech: it stays in
 * memory, is never stored, and only the last few lines are kept.
 */

import type { ConfigPatch } from "@/lib/api"
import type { CaptionEvent } from "@/lib/wire"

/** Target languages the caption service accepts (Sarvam codes). Hindi first: the default. */
export const CAPTION_LANGUAGES = ["hi", "en", "bn", "gu", "kn", "ml", "mr", "pa", "ta", "te"] as const
export type CaptionLanguage = (typeof CAPTION_LANGUAGES)[number]
export const MAX_CAPTION_LINES = 3

export const isCaptionLanguage = (code: string): code is CaptionLanguage => (CAPTION_LANGUAGES as readonly string[]).includes(code)

export interface CaptionsSetting {
  enabled: boolean
  language: string
}

/** The captions part of `GET /api/config` (`target_lang` on the wire). */
export function captionsFromConfig(config: { captions?: { enabled: boolean; target_lang: string } } | null): CaptionsSetting {
  return { enabled: config?.captions?.enabled ?? false, language: config?.captions?.target_lang ?? "hi" }
}

export function buildCaptionsPatch(change: Partial<CaptionsSetting>): ConfigPatch {
  const captions: Record<string, unknown> = {}
  if (change.enabled !== undefined) captions.enabled = change.enabled
  if (change.language !== undefined) captions.target_lang = change.language
  return { captions }
}

export interface CaptionLine {
  /** Start time on the stream clock (seconds): the identity of the line. */
  id: string
  text: string
  lang: string
  final: boolean
}

export interface CaptionState {
  lines: CaptionLine[]
}
export const initialCaptions: CaptionState = { lines: [] }

export type CaptionAction = { type: "caption"; event: CaptionEvent } | { type: "clear" }

export function captionReducer(state: CaptionState, action: CaptionAction): CaptionState {
  if (action.type === "clear") return state.lines.length === 0 ? state : initialCaptions
  const { event } = action
  const id = String(event.pts_start)
  const line: CaptionLine = { id, text: event.text, lang: event.lang, final: event.is_final }
  const index = state.lines.findIndex((l) => l.id === id)
  const lines = index >= 0 ? state.lines.map((l, i) => (i === index ? line : l)) : [...state.lines, line]
  return { lines: lines.slice(-MAX_CAPTION_LINES) }
}
