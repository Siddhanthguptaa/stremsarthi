// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, type CategorySettings, type PublicConfig } from "@/lib/api"
import { ControlsPanel } from "@/components/streamer/controls-panel"
import { deferred, renderIn } from "./dom-helpers"

const cat = (over: Partial<CategorySettings> = {}): CategorySettings => ({
  enabled: true,
  style: "blur",
  sensitivity: 0.5,
  mask_when_unsure: true,
  padding_px: 12,
  ...over,
})
const config = (): PublicConfig => ({
  categories: { face: cat(), aadhaar: cat({ style: "solid" }), plate: cat(), spoken_phone: cat() },
  captions: { enabled: false, target_lang: "hi" },
  secrets: { groq_api_key: "***", youtube_stream_key: "***", sarvam_api_key: null },
})

function makeApi() {
  return {
    getConfig: vi.fn(async () => config()),
    patchConfig: vi.fn(async (patch: { categories?: Record<string, Partial<CategorySettings>> }) => {
      const next = config()
      for (const [k, v] of Object.entries(patch.categories ?? {})) next.categories[k] = { ...next.categories[k], ...v }
      return next
    }),
  }
}

describe("ControlsPanel", () => {
  it("shows a strip per category in two groups, and never a secret", async () => {
    renderIn(<ControlsPanel api={makeApi()} />)
    expect(await screen.findByText("Face")).toBeTruthy()
    expect(screen.getByRole("heading", { name: "On screen" })).toBeTruthy()
    expect(screen.getByRole("heading", { name: "In speech" })).toBeTruthy()
    expect(screen.getByText("Aadhaar card")).toBeTruthy()
    expect(document.body.textContent).not.toContain("***")
    expect(document.body.textContent).not.toMatch(/groq|stream_key|youtube/i)
    expect(await screen.findByText("Spoken phone number")).toBeTruthy()
  })

  it("sends a PATCH with just the change and keeps it", async () => {
    const api = makeApi()
    renderIn(<ControlsPanel api={api} />)
    await userEvent.selectOptions((await screen.findAllByLabelText("Mask style"))[0], "pixelate")
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledTimes(1))
    expect(api.patchConfig).toHaveBeenCalledWith({ categories: { face: { style: "pixelate" } } })
    expect(await screen.findByText("Saved")).toBeTruthy()
    expect((screen.getAllByLabelText("Mask style")[0] as HTMLSelectElement).value).toBe("pixelate")
  })

  it("updates the card at once (optimistic) while the request is still running", async () => {
    const api = makeApi()
    const gate = deferred<PublicConfig>()
    api.patchConfig.mockImplementationOnce(() => gate.promise)
    renderIn(<ControlsPanel api={api} />)
    const select = (await screen.findAllByLabelText("Mask style"))[0] as HTMLSelectElement
    await userEvent.selectOptions(select, "solid")
    expect(select.value).toBe("solid")
    gate.resolve({ ...config(), categories: { ...config().categories, face: cat({ style: "solid" }) } })
    await screen.findByText("Saved")
  })

  it("puts the old value back and says so when the API refuses", async () => {
    const api = makeApi()
    api.patchConfig.mockRejectedValueOnce(new ApiRequestError(422, "invalid_config", "RAW DETAIL"))
    renderIn(<ControlsPanel api={api} />)
    const select = (await screen.findAllByLabelText("Mask style"))[0] as HTMLSelectElement
    await userEvent.selectOptions(select, "solid")
    expect(await screen.findByText("Could not save. The earlier value was put back.")).toBeTruthy()
    expect(select.value).toBe("blur")
    expect(document.body.textContent).not.toContain("RAW DETAIL")
  })

  it("explains a 409 requires_restart", async () => {
    const api = makeApi()
    api.patchConfig.mockRejectedValueOnce(new ApiRequestError(409, "requires_restart", "x"))
    renderIn(<ControlsPanel api={api} />)
    await userEvent.click((await screen.findAllByRole("switch", { name: "Mask when unsure" }))[0])
    expect(await screen.findByText("This change needs the engine to restart.")).toBeTruthy()
  })

  it("asks first for cards, and sends nothing until confirmed", async () => {
    const api = makeApi()
    renderIn(<ControlsPanel api={api} />)
    await userEvent.click(await screen.findByRole("switch", { name: "Protect: Aadhaar card" }))
    const dialog = await screen.findByRole("dialog")
    expect(api.patchConfig).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn off" }))
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledWith({ categories: { aadhaar: { enabled: false } } }))
  })

  it("offers a retry when the config cannot be loaded", async () => {
    const api = makeApi()
    api.getConfig.mockRejectedValueOnce(new ApiRequestError(0, "network"))
    renderIn(<ControlsPanel api={api} />)
    expect(await screen.findByText("Could not load the controls.")).toBeTruthy()
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByText("Face")).toBeTruthy()
  })
})
