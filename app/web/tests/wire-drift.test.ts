/**
 * Type-drift guard: lib/wire.ts must match the contracts.
 * Samples come from `python -m contracts.samples app/web/tests/wire_samples` (regenerate after any
 * contract change); enum values are compared with contracts/enums.py when the repo layout is present.
 */
import fs from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"

import {
  ALL_TOPICS,
  CATEGORIES,
  CHAT_ACTIONS,
  CONTRACT_MAJOR,
  decodeAs,
  decodeWire,
  HOLD_SOURCES,
  MASK_KINDS,
  MASK_STYLES,
  MODALITIES,
  parseWsFrame,
  TOPIC_TYPES,
  WIRE_FIELDS,
  WIRE_TYPE_NAMES,
  WireError,
} from "@/lib/wire"

const SAMPLES = path.join(__dirname, "wire_samples")
const sampleFiles = fs.readdirSync(SAMPLES).filter((f) => f.endsWith(".json"))
interface Sample {
  t: string
  v: number
  d: Record<string, unknown>
}
const load = (name: string): Sample => JSON.parse(fs.readFileSync(path.join(SAMPLES, `${name}.json`), "utf8"))

describe("wire samples vs lib/wire.ts", () => {
  it("has a sample for every wire type and no stray samples", () => {
    expect(sampleFiles.map((f) => f.replace(/\.json$/, "")).sort()).toEqual([...WIRE_TYPE_NAMES].sort())
  })

  it.each(WIRE_TYPE_NAMES)("%s: field names are identical", (name) => {
    const sample = load(name)
    expect(sample.t).toBe(name)
    expect(Object.keys(sample.d).sort()).toEqual(Object.keys(WIRE_FIELDS[name]).sort())
  })

  it.each(WIRE_TYPE_NAMES)("%s: decodes", (name) => {
    expect(decodeWire(load(name)).t).toBe(name)
  })

  it("rejects bad envelopes", () => {
    expect(() => decodeWire(null)).toThrow(WireError)
    expect(() => decodeWire({ t: "Nope", v: 1, d: {} })).toThrow(/unknown wire type/)
    expect(() => decodeWire({ ...load("HoldCommand"), v: 2 })).toThrow(/version/)
    expect(() => decodeWire({ t: "HoldCommand", v: 1, d: { source: "api" } })).toThrow(/missing field/)
    expect(() => decodeAs("SafetyEvent", load("HoldCommand"))).toThrow(/expected SafetyEvent/)
  })

  it("tolerates an additive unknown field", () => {
    const sample = load("HoldCommand")
    sample.d.extra_future_field = 1
    expect(decodeAs("HoldCommand", sample).active).toBe(true)
  })

  it("sample enum values are known to the client", () => {
    expect(CATEGORIES).toContain(load("SafetyEvent").d.category)
    expect(MODALITIES).toContain(load("SafetyEvent").d.modality)
    expect(HOLD_SOURCES).toContain(load("HoldCommand").d.source)
    expect(CHAT_ACTIONS).toContain(load("ChatVerdict").d.action)
    expect(MASK_STYLES).toContain(load("MaskRequest").d.style)
    expect(MASK_KINDS).toContain(load("MaskRequest").d.kind)
  })

  it("contract major version matches the engine status sample", () => {
    expect(Number(String(load("EngineStatus").d.contract_version).split(".")[0])).toBe(CONTRACT_MAJOR)
  })
})

describe("enums vs contracts/enums.py", () => {
  const enumsPy = path.resolve(__dirname, "../../../contracts/enums.py")
  const present = fs.existsSync(enumsPy)

  function pyEnum(cls: string): string[] {
    const source = fs.readFileSync(enumsPy, "utf8")
    const start = source.indexOf(`class ${cls}(StrEnum):`)
    const rest = source.slice(start + 1)
    const next = rest.search(/\n(class |# Never bridged)/)
    const body = next === -1 ? rest : rest.slice(0, next)
    return [...body.matchAll(/^\s+[A-Z_0-9]+ = "([^"]+)"/gm)].map((m) => m[1])
  }

  it.skipIf(!present).each([
    ["Category", CATEGORIES],
    ["Modality", MODALITIES],
    ["MaskStyle", MASK_STYLES],
    ["MaskKind", MASK_KINDS],
    ["ChatAction", CHAT_ACTIONS],
    ["HoldSource", HOLD_SOURCES],
    ["Topic", ALL_TOPICS],
  ] as const)("%s values are identical", (cls, values) => {
    expect([...values].sort()).toEqual(pyEnum(cls).sort())
  })
})

describe("topics and frames", () => {
  it("every browser topic is a real topic with a known wire type", () => {
    for (const [topic, type] of Object.entries(TOPIC_TYPES)) {
      expect(ALL_TOPICS).toContain(topic)
      expect(WIRE_TYPE_NAMES).toContain(type)
    }
    // engine-local topics are never visible to the browser
    for (const local of ["video.detection", "video.mask", "audio.mute", "audio.transcript"]) {
      expect(Object.keys(TOPIC_TYPES)).not.toContain(local)
    }
  })

  it("parses a data frame exactly as the API sends it", () => {
    const frame = JSON.stringify({ topic: "control.hold", seq: 4, ts: "2026-11-20T10:00:00.000+00:00", data: load("HoldCommand") })
    const parsed = parseWsFrame(frame)
    expect(parsed && "topic" in parsed && parsed.topic).toBe("control.hold")
  })

  it("rejects a frame whose payload type does not match its topic", () => {
    const frame = JSON.stringify({ topic: "safety.event", seq: 1, ts: "x", data: load("HoldCommand") })
    expect(parseWsFrame(frame)).toBeNull()
  })

  it("parses control frames and ignores junk", () => {
    expect(parseWsFrame('{"type":"ping"}')).toEqual({ type: "ping" })
    expect(parseWsFrame('{"type":"error","code":"forbidden_topics","topics":["chat.modqueue"]}')).toEqual({
      type: "error",
      code: "forbidden_topics",
      topics: ["chat.modqueue"],
    })
    expect(parseWsFrame("not json")).toBeNull()
    expect(parseWsFrame('{"topic":"video.detection","seq":1,"ts":"x","data":{}}')).toBeNull()
  })
})
