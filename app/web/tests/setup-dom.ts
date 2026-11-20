/** Test setup: jsdom gaps that Radix UI needs, and cleanup between component tests. No-op in the node environment. */
import { afterEach } from "vitest"

if (typeof window !== "undefined") {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  const g = globalThis as Record<string, unknown>
  g.ResizeObserver ??= ResizeObserverStub
  const proto = Element.prototype as unknown as Record<string, unknown>
  proto.hasPointerCapture ??= () => false
  proto.setPointerCapture ??= () => {}
  proto.releasePointerCapture ??= () => {}
  proto.scrollIntoView ??= () => {}
  window.matchMedia ??= ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia

  afterEach(async () => {
    const { cleanup } = await import("@testing-library/react")
    cleanup()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })
}
