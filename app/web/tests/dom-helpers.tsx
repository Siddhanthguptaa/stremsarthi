/** Helpers for jsdom component tests (files that start with `// @vitest-environment jsdom`). */
import * as React from "react"
import { render } from "@testing-library/react"
import { vi } from "vitest"

import { LanguageProvider } from "@/components/shell/language-provider"

export function renderIn(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>)
}

export function fakeCamera() {
  // jsdom has no media playback
  HTMLMediaElement.prototype.play = vi.fn(async () => undefined)
  const track = { stop: vi.fn() }
  const stream = { getTracks: () => [track] } as unknown as MediaStream
  const frame = () => new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: "image/jpeg" })
  const grab = vi.fn(async () => frame())
  const dispose = vi.fn()
  const deps = {
    getUserMedia: vi.fn(async () => stream),
    createGrabber: () => ({ grab, dispose }),
    sample: vi.fn(async () => ({ stats: { luma: 120, sharpness: 20 }, faces: null })),
    sleep: async (ms: number) => void ms,
  }
  return { track, stream, grab, dispose, deps }
}

export const deferred = <T = void,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
