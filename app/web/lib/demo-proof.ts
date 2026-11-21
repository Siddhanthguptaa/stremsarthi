/**
 * The "privacy proof" counter: how many things were protected, by category, since the session started.
 *
 * Only categories and counts are kept. An event is identified by its id so that the engine republishing it
 * with a duration, a reconnect replay or a REST backfill never counts it twice. No box, text or image is read.
 */

import type { ApiAlert } from "@/lib/live-state"
import type { Category, Modality, SafetyEvent } from "@/lib/wire"

/** Past this many remembered ids the memory is dropped (a rare replay may then count again). */
export const MAX_REMEMBERED_IDS = 5000

export interface ProofState {
  total: number
  byCategory: Partial<Record<Category, number>>
  byModality: Partial<Record<Modality, number>>
  seen: ReadonlySet<string>
}

export const initialProof: ProofState = { total: 0, byCategory: {}, byModality: {}, seen: new Set() }

export type ProofEvent =
  | { type: "event"; event: Pick<SafetyEvent, "event_id" | "category" | "modality" | "wall_ts"> }
  /** Earlier alerts read over REST. `since` (ms) keeps only the ones from the current session. */
  | { type: "backfill"; alerts: readonly ApiAlert[]; since: number | null }
  | { type: "reset" }

/** Status pings are not protection events. */
const isCounted = (category: Category, modality: Modality): boolean => modality !== "system" && category !== "system_status"

interface Item {
  id: string
  category: Category
  modality: Modality
}

function add(state: ProofState, items: readonly Item[]): ProofState {
  const fresh = items.filter((item, i) => !state.seen.has(item.id) && items.findIndex((o) => o.id === item.id) === i)
  if (fresh.length === 0) return state
  const seen = state.seen.size + fresh.length > MAX_REMEMBERED_IDS ? new Set<string>() : new Set(state.seen)
  const byCategory = { ...state.byCategory }
  const byModality = { ...state.byModality }
  let total = state.total
  for (const item of fresh) {
    seen.add(item.id)
    if (!isCounted(item.category, item.modality)) continue
    total += 1
    byCategory[item.category] = (byCategory[item.category] ?? 0) + 1
    byModality[item.modality] = (byModality[item.modality] ?? 0) + 1
  }
  return { total, byCategory, byModality, seen }
}

export function proofReducer(state: ProofState, event: ProofEvent): ProofState {
  switch (event.type) {
    case "reset":
      return initialProof
    case "event":
      return add(state, [{ id: event.event.event_id, category: event.event.category, modality: event.event.modality }])
    case "backfill": {
      const since = event.since
      const items = event.alerts
        .filter((a) => since === null || Date.parse(a.at) >= since)
        .map((a) => ({ id: a.id, category: a.category, modality: a.modality }))
      return add(state, items)
    }
  }
}

/** Categories sorted by count (then name), for display. */
export function proofRows(state: ProofState): { category: Category; count: number }[] {
  return (Object.entries(state.byCategory) as [Category, number][])
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category))
}
