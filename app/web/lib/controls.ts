/**
 * Category controls (docs/specs/app-web.md 5.1 `/controls`): state, optimistic edits with rollback, and
 * the rules around them. Pure functions, no React: the screen wires them to `GET|PATCH /api/config`.
 */

import type { CategorySettings, ConfigPatch, PublicConfig } from "@/lib/api"
import { ApiRequestError } from "@/lib/api"

export type CategoryChange = Partial<Pick<CategorySettings, "enabled" | "style" | "sensitivity" | "mask_when_unsure">>

/** Turning these off can put a card or a payment QR code on air, so it asks first. */
export const CONFIRM_DISABLE: ReadonlySet<string> = new Set(["aadhaar", "pan", "upi_qr"])

export type CategoryGroup = "video" | "audio"
export const GROUPS: readonly CategoryGroup[] = ["video", "audio"]

const VIDEO_ORDER = [
  "face",
  "aadhaar",
  "pan",
  "upi_qr",
  "plate",
  "signboard",
  "pin_code",
  "phone_number",
  "upi_id",
  "email",
  "otp_screen",
  "game_id",
  "room_credential",
  "risky_text",
]
const AUDIO_ORDER = ["spoken_phone", "spoken_upi", "spoken_address", "spoken_other_pii", "spoken_unverified"]

/** Which tab a category belongs to. Only categories the API returns are shown. */
export function groupOf(category: string): CategoryGroup | null {
  if (VIDEO_ORDER.includes(category)) return "video"
  if (category.startsWith("spoken_")) return "audio"
  return null
}

/** Category keys of one group, in a stable, sensible order (unknown ones last, alphabetical). */
export function categoriesInGroup(categories: Record<string, CategorySettings>, group: CategoryGroup): string[] {
  const order = group === "video" ? VIDEO_ORDER : AUDIO_ORDER
  const keys = Object.keys(categories).filter((key) => groupOf(key) === group)
  return keys.sort((a, b) => {
    const ia = order.indexOf(a)
    const ib = order.indexOf(b)
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
    return a.localeCompare(b)
  })
}

export function needsDisableConfirm(category: string, change: CategoryChange): boolean {
  return CONFIRM_DISABLE.has(category) && change.enabled === false
}

/** `PATCH /api/config` body for one category change. */
export function buildPatch(category: string, change: CategoryChange): ConfigPatch {
  return { categories: { [category]: change } }
}

// --- speech languages (`audio.languages`) -----------------------------------------------------------------

/** What the speech pipeline can transcribe; the API sends the same list as `audio.supported_languages`. */
export const DEFAULT_SPEECH_LANGUAGES = ["hi", "en", "kn", "ta", "te", "bn"] as const

/** `PATCH /api/config` body for a new language list. */
export const buildLanguagesPatch = (languages: readonly string[]): ConfigPatch => ({ audio: { languages: [...languages] } })

/**
 * Turn one language on or off. The result keeps the supported order. The last language cannot be turned
 * off (the speech check needs at least one), so that returns `null` and nothing is sent.
 */
export function toggleLanguage(current: readonly string[], code: string, on: boolean, supported: readonly string[]): string[] | null {
  const set = new Set(current)
  if (on) set.add(code)
  else set.delete(code)
  if (set.size === 0) return null
  return supported.filter((c) => set.has(c))
}

export const sensitivityPercent = (value: number): number => Math.round(value * 100)

// --- state --------------------------------------------------------------------------------------------

export type ControlsNotice =
  | { kind: "saved"; category: string }
  | { kind: "reverted"; category: string; reason: "restart" | "failed" }

export interface ControlsState {
  status: "loading" | "ready" | "error"
  categories: Record<string, CategorySettings>
  /** Edits sent and not yet answered, by edit id. */
  inflight: Record<number, { category: string; previous: CategoryChange }>
  /** Speech languages being checked, and the ones that can be chosen. */
  languages: string[]
  supportedLanguages: string[]
  /** Language edits sent and not yet answered, by edit id. */
  languagesInflight: Record<number, { previous: string[] }>
  notice: ControlsNotice | null
}

export const initialControlsState: ControlsState = {
  status: "loading",
  categories: {},
  inflight: {},
  languages: [],
  supportedLanguages: [...DEFAULT_SPEECH_LANGUAGES],
  languagesInflight: {},
  notice: null,
}

export type ControlsAction =
  | { type: "loaded"; config: PublicConfig }
  | { type: "load-failed" }
  | { type: "edit"; id: number; category: string; change: CategoryChange }
  | { type: "saved"; id: number; config: PublicConfig }
  | { type: "failed"; id: number; reason: "restart" | "failed" }
  | { type: "languages-edit"; id: number; languages: string[] }
  | { type: "languages-saved"; id: number; config: PublicConfig }
  | { type: "languages-failed"; id: number; reason: "restart" | "failed" }
  | { type: "dismiss-notice" }

export function controlsReducer(state: ControlsState, action: ControlsAction): ControlsState {
  switch (action.type) {
    case "loaded":
      return {
        ...state,
        status: "ready",
        categories: action.config.categories ?? {},
        languages: action.config.audio?.languages ?? state.languages,
        supportedLanguages: action.config.audio?.supported_languages ?? state.supportedLanguages,
      }
    case "load-failed":
      return { ...state, status: state.status === "ready" ? "ready" : "error" }
    case "edit": {
      const current = state.categories[action.category]
      if (!current) return state
      const previous: CategoryChange = {}
      for (const key of Object.keys(action.change) as (keyof CategoryChange)[]) {
        ;(previous as Record<string, unknown>)[key] = current[key]
      }
      return {
        ...state,
        notice: null,
        categories: { ...state.categories, [action.category]: { ...current, ...action.change } },
        inflight: { ...state.inflight, [action.id]: { category: action.category, previous } },
      }
    }
    case "saved": {
      const sent = state.inflight[action.id]
      if (!sent) return state
      const { [action.id]: _done, ...rest } = state.inflight
      void _done
      const stillPending = Object.values(rest).some((p) => p.category === sent.category)
      const server = action.config.categories?.[sent.category]
      return {
        ...state,
        inflight: rest,
        // The API echoes the saved value: take it, unless a newer edit of the same card is on its way.
        categories: server && !stillPending ? { ...state.categories, [sent.category]: server } : state.categories,
        notice: { kind: "saved", category: sent.category },
      }
    }
    case "failed": {
      const sent = state.inflight[action.id]
      if (!sent) return state
      const { [action.id]: _done, ...rest } = state.inflight
      void _done
      const current = state.categories[sent.category]
      return {
        ...state,
        inflight: rest,
        categories: current ? { ...state.categories, [sent.category]: { ...current, ...sent.previous } } : state.categories,
        notice: { kind: "reverted", category: sent.category, reason: action.reason },
      }
    }
    case "languages-edit":
      return {
        ...state,
        notice: null,
        languages: action.languages,
        languagesInflight: { ...state.languagesInflight, [action.id]: { previous: state.languages } },
      }
    case "languages-saved": {
      if (!state.languagesInflight[action.id]) return state
      const { [action.id]: _done, ...rest } = state.languagesInflight
      void _done
      // The API echoes the saved list: take it, unless a newer edit is on its way.
      const server = action.config.audio?.languages
      return {
        ...state,
        languagesInflight: rest,
        languages: server && Object.keys(rest).length === 0 ? server : state.languages,
        notice: { kind: "saved", category: "languages" },
      }
    }
    case "languages-failed": {
      const sent = state.languagesInflight[action.id]
      if (!sent) return state
      const { [action.id]: _done, ...rest } = state.languagesInflight
      void _done
      return {
        ...state,
        languagesInflight: rest,
        // Put back the list from before this edit, unless a later edit already replaced it.
        languages: Object.keys(rest).length === 0 ? sent.previous : state.languages,
        notice: { kind: "reverted", category: "languages", reason: action.reason },
      }
    }
    case "dismiss-notice":
      return { ...state, notice: null }
  }
}

export function isSaving(state: ControlsState, category: string): boolean {
  return Object.values(state.inflight).some((p) => p.category === category)
}

/** A 409 `requires_restart` is not a failure of the request, it is a rule: say so. */
export function failureReason(error: unknown): "restart" | "failed" {
  return error instanceof ApiRequestError && error.status === 409 && error.code === "requires_restart" ? "restart" : "failed"
}
