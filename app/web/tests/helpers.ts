/** Test helpers: build server frames from the contract wire samples (so they stay valid as contracts evolve). */
import fs from "node:fs"
import path from "node:path"

import type { HoldSource } from "@/lib/wire"

const SAMPLES = path.join(__dirname, "wire_samples")
const sample = (name: string): { t: string; v: number; d: Record<string, unknown> } =>
  JSON.parse(fs.readFileSync(path.join(SAMPLES, `${name}.json`), "utf8"))

export function dumpsEnvelope(type: string, data: Record<string, unknown>): string {
  return JSON.stringify({ t: type, v: 1, d: data })
}

export function holdFrame(seq: number, source: HoldSource, active: boolean) {
  return {
    topic: "control.hold",
    seq,
    ts: "2026-11-20T10:00:00.000+00:00",
    data: { ...sample("HoldCommand"), d: { source, active, actor: "abc123" } },
  }
}

export function safetyFrame(seq: number, over: Record<string, unknown> = {}) {
  const base = sample("SafetyEvent")
  return {
    topic: "safety.event",
    seq,
    ts: "2026-11-20T10:00:00.000+00:00",
    data: { ...base, d: { ...base.d, event_id: `evt-${seq}`, ...over } },
  }
}

export function engineFrame(seq: number, over: Record<string, unknown> = {}) {
  const base = sample("EngineStatus")
  return {
    topic: "engine.status",
    seq,
    ts: "2026-11-20T10:00:00.000+00:00",
    data: { ...base, d: { ...base.d, ...over } },
  }
}

export const sampleData = <T = Record<string, unknown>>(name: string): T => sample(name).d as T
