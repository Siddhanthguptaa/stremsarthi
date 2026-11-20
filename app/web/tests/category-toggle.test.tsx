// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import type { CategorySettings } from "@/lib/api"
import { CategoryToggle } from "@/components/streamer/category-toggle"
import { renderIn } from "./dom-helpers"

const settings = (over: Partial<CategorySettings> = {}): CategorySettings => ({
  enabled: true,
  style: "blur",
  sensitivity: 0.5,
  mask_when_unsure: true,
  padding_px: 12,
  ...over,
})

describe("CategoryToggle", () => {
  it("has a labelled switch, a style select, a sensitivity slider and mask-when-unsure", () => {
    renderIn(<CategoryToggle category="face" settings={settings()} onChange={vi.fn()} />)
    const card = screen.getByText("Face").closest("[data-category]") as HTMLElement
    expect(within(card).getByRole("switch", { name: "Protect: Face" })).toBeTruthy()
    expect((within(card).getByLabelText("Mask style") as HTMLSelectElement).value).toBe("blur")
    const slider = within(card).getByRole("slider", { name: "Sensitivity: Face" })
    expect(slider.getAttribute("aria-valuenow")).toBe("0.5")
    expect(slider.getAttribute("aria-valuetext")).toBe("50 %")
    expect(within(card).getByRole("switch", { name: "Mask when unsure" })).toBeTruthy()
    expect(within(card).getAllByText("50 %").length).toBeGreaterThan(0)
  })

  it("changes the style and mask-when-unsure with one change each", async () => {
    const onChange = vi.fn()
    renderIn(<CategoryToggle category="plate" settings={settings()} onChange={onChange} />)
    await userEvent.selectOptions(screen.getByLabelText("Mask style"), "pixelate")
    expect(onChange).toHaveBeenLastCalledWith("plate", { style: "pixelate" })
    await userEvent.click(screen.getByRole("switch", { name: "Mask when unsure" }))
    expect(onChange).toHaveBeenLastCalledWith("plate", { mask_when_unsure: false })
  })

  it("moves the sensitivity with the keyboard and sends it once per step", async () => {
    const onChange = vi.fn()
    renderIn(<CategoryToggle category="face" settings={settings()} onChange={onChange} />)
    const slider = screen.getByRole("slider", { name: "Sensitivity: Face" })
    slider.focus()
    fireEvent.keyDown(slider, { key: "ArrowRight" })
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))
    expect(onChange.mock.calls[0][0]).toBe("face")
    expect(onChange.mock.calls[0][1].sensitivity).toBeCloseTo(0.55)
  })

  it("turns an ordinary category off straight away", async () => {
    const onChange = vi.fn()
    renderIn(<CategoryToggle category="face" settings={settings()} onChange={onChange} />)
    await userEvent.click(screen.getByRole("switch", { name: "Protect: Face" }))
    expect(onChange).toHaveBeenCalledWith("face", { enabled: false })
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it.each([
    ["aadhaar", "Aadhaar card"],
    ["pan", "PAN card"],
    ["upi_qr", "UPI QR code"],
  ])("asks before turning %s off, and only turns it off after Turn off", async (category, name) => {
    const onChange = vi.fn()
    renderIn(<CategoryToggle category={category} settings={settings()} onChange={onChange} />)
    await userEvent.click(screen.getByRole("switch", { name: `Protect: ${name}` }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Cards and QR codes can show on stream/)).toBeTruthy()
    expect(onChange).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn off" }))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith(category, { enabled: false })
  })

  it("keeps the protection on when the confirmation is cancelled", async () => {
    const onChange = vi.fn()
    renderIn(<CategoryToggle category="aadhaar" settings={settings()} onChange={onChange} />)
    await userEvent.click(screen.getByRole("switch", { name: "Protect: Aadhaar card" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Keep on" }))
    expect(onChange).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("turning a card category back on needs no confirmation", async () => {
    const onChange = vi.fn()
    renderIn(<CategoryToggle category="pan" settings={settings({ enabled: false })} onChange={onChange} />)
    await userEvent.click(screen.getByRole("switch", { name: "Protect: PAN card" }))
    expect(onChange).toHaveBeenCalledWith("pan", { enabled: true })
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("disables the detail controls while the category is off", () => {
    renderIn(<CategoryToggle category="face" settings={settings({ enabled: false })} onChange={vi.fn()} />)
    expect((screen.getByLabelText("Mask style") as HTMLSelectElement).disabled).toBe(true)
    expect(screen.getByRole("switch", { name: "Mask when unsure" }).hasAttribute("disabled")).toBe(true)
    expect(screen.getByRole("slider", { name: "Sensitivity: Face" }).getAttribute("data-disabled")).not.toBeNull()
  })

  it("falls back to the category code for one it does not know", () => {
    renderIn(<CategoryToggle category="new_thing" settings={settings()} onChange={vi.fn()} />)
    expect(screen.getByText("new_thing")).toBeTruthy()
  })
})
