import { describe, expect, it } from "vitest"

import { modEn, modHi, modText, reportEn, reportHi, reportText } from "@/lib/screen-text"

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

const pairs: [string, Record<string, string>, Record<string, string>][] = [
  ["report", reportEn, reportHi],
  ["moderator", modEn, modHi],
]

describe.each(pairs)("%s text", (_name, en, hi) => {
  it("has a Hindi string for every English key, and no extras", () => {
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort())
  })
  it("has no empty strings and the same placeholders in both languages", () => {
    for (const key of Object.keys(en)) {
      expect(en[key].length, key).toBeGreaterThan(0)
      expect(hi[key].length, key).toBeGreaterThan(0)
      expect(placeholders(hi[key]), key).toEqual(placeholders(en[key]))
    }
  })
})

describe("text lookup", () => {
  it("fills placeholders and picks the language", () => {
    expect(reportText("en", "report.events", { n: 3 })).toBe("3 events")
    expect(reportText("hi", "report.events", { n: 3 })).toBe("3 घटनाएँ")
    expect(modText("en", "modx.selected", { n: 2, total: 5 })).toBe("Selected message 2 of 5")
  })
})
