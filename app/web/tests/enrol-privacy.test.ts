import fs from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"

/**
 * Static privacy guard for the enrolment code (docs/specs/app-web.md 5.4 and 5.7): no stored or logged
 * frames, labels or tokens, and nothing that can render a picture of a face.
 */
const ROOT = path.resolve(__dirname, "..")
const files = [
  "lib/enrol.ts",
  "lib/capture.ts",
  "components/enrollment/camera-capture.tsx",
  "components/enrollment/enrol-panel.tsx",
  "components/enrollment/face-card.tsx",
]
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), "utf8")
// comments may mention the words; code may not use them
const code = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/.*$/gm, "")

describe("enrolment code privacy", () => {
  it.each(files)("%s does not log, persist or render pictures", (rel) => {
    const src = code(rel)
    expect(src).not.toMatch(/console\./)
    expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB|caches\.|document\.cookie/)
    expect(src).not.toMatch(/toDataURL|createObjectURL|FileReader/)
    expect(src).not.toMatch(/<img\b|next\/image|<Image\b|AvatarImage/)
  })

  it("only the camera module requests the camera, video only, and only the grabber touches a canvas", () => {
    const users = files.filter((f) => /navigator\.mediaDevices/.test(code(f)))
    expect(users).toEqual(["lib/capture.ts"]) // the default dependency; the component calls it through deps
    expect(code("components/enrollment/camera-capture.tsx")).toMatch(/audio: false/)
    expect(files.filter((f) => /createElement\("canvas"\)/.test(code(f)))).toEqual(["lib/capture.ts"])
  })

  it("the capture helpers clear the canvas and stop the tracks", () => {
    const capture = code("lib/capture.ts")
    expect(capture).toMatch(/clearRect/)
    expect(capture).toMatch(/\.width = 0/)
    expect(capture).toMatch(/track\.stop\(\)/)
  })

  it("the API client never puts the label or a token in a URL", () => {
    const api = code("lib/api.ts")
    expect(api).not.toMatch(/\?token=|\?label=|&label=/)
  })
})
