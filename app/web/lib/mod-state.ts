/**
 * State of the moderator console (/mod).
 *
 * Source of truth is the API's WebSocket: `chat.modqueue` items (pending ones carry the message text),
 * `chat.verdict` (the platform result of an action) and, for a console that connects late, a replay of the
 * pending queue after a marker that resets this state.
 *
 * Privacy: text exists only inside a pending item, in memory. The moment an item is resolved (approved,
 * deleted, timed out, dismissed) or expires (the API drops it after 30 minutes, this state after the same
 * time since it last saw it) the text is dropped and only a stub with category, state and result remains.
 */

import type { Category, ChatAction, ChatMessage, ChatVerdict, ModQueueItem, Platform } from "@/lib/wire"

export const MOD_TTL_MS = 1_800_000
export const RESOLVED_KEEP = 20
export const TIMEOUT_PRESETS = [60, 600, 3600] as const
export type TimeoutPreset = (typeof TIMEOUT_PRESETS)[number]
export type ModActionKind = "approve" | "delete" | "timeout" | "dismiss"
export type PlatformResult = ChatVerdict["platform_result"]
export type ResolvedState = Exclude<ModQueueItem["state"], "pending">

export interface QueueEntry {
  key: string
  platform: Platform
  messageId: string
  author: string
  text: string
  /** Epoch seconds the chat process queued it. */
  enqueuedAt: number
  categories: Category[]
  confidence: number
  /** What the shield decided (hide, hold_for_mod, delete, timeout, or allow for a flagged staff message). */
  action: ChatAction
  platformResult: PlatformResult
  /** Messages from the broadcaster or a moderator: they can be approved or dismissed, never removed. */
  staff: boolean
  /** `Date.now()` of the last update from the server, for the expiry. */
  seenAt: number
  /** An action this console sent and the chat process has not answered yet. */
  busy: ModActionKind | null
}

/** What is left of a resolved item: no text, no author. */
export interface ResolvedStub {
  key: string
  platform: Platform
  state: ResolvedState
  categories: Category[]
  platformResult: PlatformResult
}

export interface ModState {
  /** Pending items, in arrival order (the view sorts newest first). */
  items: QueueEntry[]
  resolved: ResolvedStub[]
  hiddenCount: number
}

export const initialModState: ModState = { items: [], resolved: [], hiddenCount: 0 }

export type ModEvent =
  | { type: "item"; item: ModQueueItem; at: number }
  | { type: "verdict"; verdict: ChatVerdict }
  | { type: "hidden-count"; count: number }
  | { type: "reset"; hiddenCount: number }
  | { type: "busy"; key: string; action: ModActionKind }
  | { type: "busy-clear"; key: string; action?: ModActionKind }
  | { type: "expire"; now: number }

export const entryKey = (platform: string, id: string): string => `${platform}:${id}`

function toEntry(item: ModQueueItem, at: number, busy: ModActionKind | null): QueueEntry {
  const m: ChatMessage = item.message
  return {
    key: entryKey(m.platform, m.message_id),
    platform: m.platform,
    messageId: m.message_id,
    author: m.author_display,
    text: m.text,
    enqueuedAt: item.enqueued_at,
    categories: item.verdict.categories,
    confidence: item.verdict.confidence,
    action: item.verdict.action,
    platformResult: item.verdict.platform_result,
    staff: m.is_broadcaster || m.is_moderator,
    seenAt: at,
    busy,
  }
}

function pushResolved(list: ResolvedStub[], stub: ResolvedStub): ResolvedStub[] {
  const next = [stub, ...list.filter((s) => s.key !== stub.key)]
  if (next.length > RESOLVED_KEEP) next.length = RESOLVED_KEEP
  return next
}

export function modReducer(state: ModState, event: ModEvent): ModState {
  switch (event.type) {
    case "reset":
      return { items: [], resolved: [], hiddenCount: event.hiddenCount }
    case "hidden-count":
      return state.hiddenCount === event.count ? state : { ...state, hiddenCount: event.count }
    case "item": {
      const { item } = event
      const key = entryKey(item.message.platform, item.message.message_id)
      const old = state.items.find((e) => e.key === key)
      if (item.state === "pending") {
        const entry = toEntry(item, event.at, old?.busy ?? null)
        // A verdict can be newer than the item's own copy of it (a failed delete): keep the worse-known result.
        if (old && old.platformResult !== "pending" && entry.platformResult === "pending") {
          entry.platformResult = old.platformResult
        }
        const items = old ? state.items.map((e) => (e.key === key ? entry : e)) : [...state.items, entry]
        return { ...state, items }
      }
      // Resolved: the text goes now.
      const stub: ResolvedStub = {
        key,
        platform: item.message.platform,
        state: item.state,
        categories: item.verdict.categories,
        platformResult: old && old.platformResult !== "pending" ? old.platformResult : item.verdict.platform_result,
      }
      return { ...state, items: state.items.filter((e) => e.key !== key), resolved: pushResolved(state.resolved, stub) }
    }
    case "verdict": {
      const v = event.verdict
      const key = entryKey(v.platform, v.message_id)
      if (state.items.some((e) => e.key === key)) {
        // The chat process answered: the item may be acted on again (a failed platform call leaves it open).
        const items = state.items.map((e) => (e.key === key ? { ...e, platformResult: v.platform_result, busy: null } : e))
        return { ...state, items }
      }
      if (state.resolved.some((s) => s.key === key)) {
        return { ...state, resolved: state.resolved.map((s) => (s.key === key ? { ...s, platformResult: v.platform_result } : s)) }
      }
      return state
    }
    case "busy":
      return { ...state, items: state.items.map((e) => (e.key === event.key ? { ...e, busy: event.action } : e)) }
    case "busy-clear": {
      const hit = state.items.find((e) => e.key === event.key && e.busy !== null && (!event.action || e.busy === event.action))
      if (!hit) return state
      return { ...state, items: state.items.map((e) => (e === hit ? { ...e, busy: null } : e)) }
    }
    case "expire": {
      const items = state.items.filter((e) => event.now - e.seenAt <= MOD_TTL_MS)
      return items.length === state.items.length ? state : { ...state, items }
    }
  }
}

// --- the view ---------------------------------------------------------------------------------------------

export interface ModFilters {
  platform: Platform | "all"
  category: Category | "all"
}
export const NO_FILTERS: ModFilters = { platform: "all", category: "all" }

/** Newest first, then filtered. */
export function visibleItems(items: readonly QueueEntry[], filters: ModFilters): QueueEntry[] {
  return items
    .filter((e) => filters.platform === "all" || e.platform === filters.platform)
    .filter((e) => filters.category === "all" || e.categories.includes(filters.category as Category))
    .slice()
    .sort((a, b) => b.enqueuedAt - a.enqueuedAt || 0)
}

/** Categories present in the queue, for the filter chips. */
export function presentCategories(items: readonly QueueEntry[]): Category[] {
  const seen = new Set<Category>()
  for (const e of items) for (const c of e.categories) seen.add(c)
  return [...seen].sort()
}

export interface KeyAction {
  kind: "move" | "act"
  delta?: 1 | -1
  action?: ModActionKind
}

/** Console shortcuts: J/K move, A approve, D delete, T timeout, X dismiss. Ignored while typing or with modifiers. */
export function keyAction(event: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "target">): KeyAction | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null
  const target = event.target as HTMLElement | null
  const tag = target?.tagName
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return null
  switch (event.key.toLowerCase()) {
    case "j":
      return { kind: "move", delta: 1 }
    case "k":
      return { kind: "move", delta: -1 }
    case "a":
      return { kind: "act", action: "approve" }
    case "d":
      return { kind: "act", action: "delete" }
    case "t":
      return { kind: "act", action: "timeout" }
    case "x":
      return { kind: "act", action: "dismiss" }
    default:
      return null
  }
}

/** Which selected entry after the list changed: keep it, else the neighbour at the same position, else none. */
export function pickSelection(visible: readonly QueueEntry[], selectedKey: string | null, lastIndex: number): string | null {
  if (visible.length === 0) return null
  if (selectedKey && visible.some((e) => e.key === selectedKey)) return selectedKey
  return visible[Math.min(Math.max(lastIndex, 0), visible.length - 1)].key
}
