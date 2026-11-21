// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import type { ConfigPatch, PublicConfig } from "@/lib/api"
import { CaptionsControl } from "@/components/streamer/captions-control"
import { renderIn } from "./dom-helpers"

const config = (enabled = false, lang = "hi"): PublicConfig => ({ categories: {}, captions: { enabled, target_lang: lang } })

function makeApi(initial = config()) {
  return {
    getConfig: vi.fn(async () => initial),
    patchConfig: vi.fn(async (patch: ConfigPatch) => {
      const c = (patch.captions ?? {}) as { enabled?: boolean; target_lang?: string }
      return config(c.enabled ?? initial.captions?.enabled ?? false, c.target_lang ?? initial.captions?.target_lang ?? "hi")
    }),
  }
}

describe("CaptionsControl", () => {
  it("loads the saved state and labels both controls", async () => {
    renderIn(<CaptionsControl api={makeApi(config(true, "ta"))} />)
    const toggle = await screen.findByRole("switch", { name: "Show captions" })
    expect(toggle.getAttribute("aria-checked")).toBe("true")
    expect((screen.getByLabelText("Caption language") as HTMLSelectElement).value).toBe("ta")
  })

  it("sends one captions.enabled patch and shows Saved", async () => {
    const api = makeApi()
    renderIn(<CaptionsControl api={api} />)
    await userEvent.click(await screen.findByRole("switch", { name: "Show captions" }))
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledTimes(1))
    expect(api.patchConfig).toHaveBeenCalledWith({ captions: { enabled: true } })
    expect(await screen.findByText("Saved")).toBeTruthy()
    expect(screen.getByRole("switch", { name: "Show captions" }).getAttribute("aria-checked")).toBe("true")
  })

  it("changes the language with target_lang", async () => {
    const api = makeApi(config(true))
    renderIn(<CaptionsControl api={api} />)
    await userEvent.selectOptions(await screen.findByLabelText("Caption language"), "kn")
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledWith({ captions: { target_lang: "kn" } }))
  })

  it("goes back and says so when the API refuses", async () => {
    const api = makeApi()
    api.patchConfig.mockRejectedValueOnce(new Error("409"))
    renderIn(<CaptionsControl api={api} />)
    await userEvent.click(await screen.findByRole("switch", { name: "Show captions" }))
    expect(await screen.findByText("Could not change captions. Try again.")).toBeTruthy()
    expect(screen.getByRole("switch", { name: "Show captions" }).getAttribute("aria-checked")).toBe("false")
  })

  it("shows the latest caption lines while captions are on", async () => {
    const lines = [{ id: "1", text: "नमस्ते दोस्तों", lang: "hi", final: true }]
    renderIn(<CaptionsControl api={makeApi(config(true))} lines={lines} />)
    expect((await screen.findByTestId("caption-lines")).textContent).toBe("नमस्ते दोस्तों")
  })

  it("shows no caption text while captions are off", async () => {
    const lines = [{ id: "1", text: "नमस्ते दोस्तों", lang: "hi", final: true }]
    renderIn(<CaptionsControl api={makeApi(config(false))} lines={lines} />)
    expect(await screen.findByText("Captions are off.")).toBeTruthy()
    expect(screen.queryByTestId("caption-lines")).toBeNull()
  })

  it("reports a failed load", async () => {
    const api = { getConfig: vi.fn(async () => Promise.reject(new Error("down"))), patchConfig: vi.fn() }
    renderIn(<CaptionsControl api={api} />)
    expect(await screen.findByText("Caption settings could not be loaded.")).toBeTruthy()
  })
})
