// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, type CategorySettings, type ConfigPatch, type PublicConfig } from "@/lib/api"
import {
  buildLanguagesPatch,
  controlsReducer,
  DEFAULT_SPEECH_LANGUAGES,
  initialControlsState,
  toggleLanguage,
} from "@/lib/controls"
import { ControlsPanel } from "@/components/streamer/controls-panel"
import { renderIn } from "./dom-helpers"

const cat = (): CategorySettings => ({ enabled: true, style: "blur", sensitivity: 0.5, mask_when_unsure: true, padding_px: 12 })
const SUPPORTED = ["hi", "en", "kn", "ta", "te", "bn"]
const config = (languages = ["hi", "en"]): PublicConfig => ({
  categories: { face: cat(), spoken_phone: cat(), spoken_upi: cat(), spoken_address: cat(), spoken_other_pii: cat() },
  audio: { languages, supported_languages: SUPPORTED },
})

describe("toggleLanguage", () => {
  it("adds in the supported order and removes by code", () => {
    expect(toggleLanguage(["hi", "ta"], "en", true, SUPPORTED)).toEqual(["hi", "en", "ta"])
    expect(toggleLanguage(["hi", "en"], "hi", false, SUPPORTED)).toEqual(["en"])
  })
  it("never turns the last language off", () => {
    expect(toggleLanguage(["hi"], "hi", false, SUPPORTED)).toBeNull()
  })
  it("builds a JSON merge patch and the default list matches the API's", () => {
    expect(buildLanguagesPatch(["hi", "kn"])).toEqual({ audio: { languages: ["hi", "kn"] } })
    expect([...DEFAULT_SPEECH_LANGUAGES]).toEqual(SUPPORTED)
  })
})

describe("language edits in the reducer", () => {
  const loaded = controlsReducer(initialControlsState, { type: "loaded", config: config() })
  it("loads the list and what can be chosen", () => {
    expect(loaded.languages).toEqual(["hi", "en"])
    expect(loaded.supportedLanguages).toEqual(SUPPORTED)
  })
  it("applies at once, takes the echo, and rolls back on failure", () => {
    const edited = controlsReducer(loaded, { type: "languages-edit", id: 1, languages: ["hi", "en", "ta"] })
    expect(edited.languages).toEqual(["hi", "en", "ta"])
    const saved = controlsReducer(edited, { type: "languages-saved", id: 1, config: config(["hi", "en", "ta"]) })
    expect(saved.languages).toEqual(["hi", "en", "ta"])
    expect(saved.notice).toEqual({ kind: "saved", category: "languages" })
    const failed = controlsReducer(edited, { type: "languages-failed", id: 1, reason: "failed" })
    expect(failed.languages).toEqual(["hi", "en"])
    expect(failed.notice).toMatchObject({ kind: "reverted", reason: "failed" })
    expect(failed.languagesInflight).toEqual({})
  })
  it("ignores an answer for an unknown edit", () => {
    expect(controlsReducer(loaded, { type: "languages-saved", id: 9, config: config() })).toBe(loaded)
    expect(controlsReducer(loaded, { type: "languages-failed", id: 9, reason: "failed" })).toBe(loaded)
  })
})

function makeApi() {
  return {
    getConfig: vi.fn(async () => config()),
    patchConfig: vi.fn(async (patch: ConfigPatch) =>
      config((patch.audio as { languages?: string[] } | undefined)?.languages ?? ["hi", "en"]),
    ),
  }
}

describe("audio tab", () => {
  const openAudio = async (api: ReturnType<typeof makeApi>) => {
    renderIn(<ControlsPanel api={api} />)
    return await screen.findByRole("group", { name: /Speech languages/ })
  }

  it("lists the four audio toggles and the languages, with no mask-style choice for speech", async () => {
    const group = await openAudio(makeApi())
    for (const name of ["Spoken phone number", "Spoken UPI ID", "Spoken address", "Spoken personal detail"]) {
      expect(screen.getByText(name)).toBeTruthy()
    }
    // Both groups are on screen now: only the speech strips must have no mask-style choice.
    const speech = Array.from(document.querySelectorAll<HTMLElement>('[data-category^="spoken_"]'))
    expect(speech).toHaveLength(4)
    for (const strip of speech) expect(within(strip).queryByLabelText("Mask style")).toBeNull()
    expect(screen.getAllByRole("switch", { name: "Mute when unsure" })).toHaveLength(4)
    expect(within(group).getAllByRole("switch")).toHaveLength(6)
    expect((within(group).getByRole("switch", { name: /English/ }) as HTMLElement).getAttribute("aria-checked")).toBe("true")
    expect((within(group).getByRole("switch", { name: /Tamil/ }) as HTMLElement).getAttribute("aria-checked")).toBe("false")
  })

  it("turning a language on sends one audio.languages patch and keeps it", async () => {
    const api = makeApi()
    const group = await openAudio(api)
    await userEvent.click(within(group).getByRole("switch", { name: /Kannada/ }))
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledTimes(1))
    expect(api.patchConfig).toHaveBeenCalledWith({ audio: { languages: ["hi", "en", "kn"] } })
    expect(await screen.findByText("Saved")).toBeTruthy()
    expect(within(group).getByRole("switch", { name: /Kannada/ }).getAttribute("aria-checked")).toBe("true")
  })

  it("keeps the last language on (the switch is disabled and says why)", async () => {
    const api = makeApi()
    api.getConfig.mockResolvedValueOnce(config(["hi"]))
    const group = await openAudio(api)
    const hindi = within(group).getByRole("switch", { name: /Hindi/ }) as HTMLButtonElement
    expect(hindi.disabled).toBe(true)
    expect(screen.getByText("At least one language must stay on.")).toBeTruthy()
    await userEvent.click(hindi)
    expect(api.patchConfig).not.toHaveBeenCalled()
  })

  it("puts the list back when the API refuses", async () => {
    const api = makeApi()
    api.patchConfig.mockRejectedValueOnce(new ApiRequestError(422, "invalid_config", "RAW DETAIL"))
    const group = await openAudio(api)
    await userEvent.click(within(group).getByRole("switch", { name: /Telugu/ }))
    expect(await screen.findByText("Could not save. The earlier value was put back.")).toBeTruthy()
    expect(within(group).getByRole("switch", { name: /Telugu/ }).getAttribute("aria-checked")).toBe("false")
    expect(document.body.textContent).not.toContain("RAW DETAIL")
  })

  it("an audio toggle patches just that category", async () => {
    const api = makeApi()
    await openAudio(api)
    await userEvent.click(screen.getByRole("switch", { name: "Protect: Spoken UPI ID" }))
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledWith({ categories: { spoken_upi: { enabled: false } } }))
  })
})
