/**
 * State of the streamer's chat overlay on /live.
 *
 * What the overlay may hold: messages the API delivered on `chat.allowed` (final verdict ALLOW, or a
 * moderator approved them) and a number, `hiddenCount`. The streamer role never receives the text of a hidden,
 * held, deleted or timed-out message: the API enforces it, and this state has no field that could carry one.
 * Only what the screen shows is kept (author name, text, time, staff flag): author and channel ids are dropped
 * on arrival. The list is capped and lives in memory only.
 */

import type { ChatMessage, Platform } from "@/lib/wire"

export const OVERLAY_MAX_MESSAGES = 100
/** Very long chat lines wrap and are shown up to this many characters (docs/specs/app-web.md section 7). */
export const MAX_SHOWN_CHARS = 300

export interface OverlayMessage {
  /** `platform:message_id`, unique per message. */
  key: string
  platform: Platform
  author: string
  text: string
  /** Epoch seconds from the platform. */
  receivedAt: number
  broadcaster: boolean
  moderator: boolean
}

export interface ChatOverlayState {
  /** Newest first. */
  messages: OverlayMessage[]
  /** Messages whose latest verdict is not ALLOW (as counted by the API). */
  hiddenCount: number
  /** Messages seen in total this page view. */
  total: number
}

export const initialChatOverlay: ChatOverlayState = { messages: [], hiddenCount: 0, total: 0 }

export type ChatOverlayEvent =
  | { type: "allowed"; message: ChatMessage }
  | { type: "hidden-count"; count: number }
  /** The server is about to replay the current state: start again. */
  | { type: "reset"; hiddenCount: number }

/** Wrap-friendly clip: at most `MAX_SHOWN_CHARS` characters, with an ellipsis when cut. */
export function clipText(text: string, max = MAX_SHOWN_CHARS): string {
  const chars = Array.from(text) // count characters, not UTF-16 units, so emoji and Devanagari are not split
  return chars.length <= max ? text : `${chars.slice(0, max - 1).join("")}…`
}

export const messageKey = (platform: string, id: string): string => `${platform}:${id}`

export function chatOverlayReducer(state: ChatOverlayState, event: ChatOverlayEvent): ChatOverlayState {
  switch (event.type) {
    case "hidden-count":
      return state.hiddenCount === event.count ? state : { ...state, hiddenCount: event.count }
    case "reset":
      return { messages: [], hiddenCount: event.hiddenCount, total: 0 }
    case "allowed": {
      const m = event.message
      const key = messageKey(m.platform, m.message_id)
      if (state.messages.some((x) => x.key === key)) return state // a reconnect can repeat a message
      const next: OverlayMessage = {
        key,
        platform: m.platform,
        author: m.author_display,
        text: m.text,
        receivedAt: m.received_at,
        broadcaster: m.is_broadcaster,
        moderator: m.is_moderator,
      }
      const messages = [next, ...state.messages]
      if (messages.length > OVERLAY_MAX_MESSAGES) messages.length = OVERLAY_MAX_MESSAGES
      return { ...state, messages, total: state.total + 1 }
    }
  }
}
