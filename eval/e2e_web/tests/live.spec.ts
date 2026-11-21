import { expect, test } from "@playwright/test"
import { getJson, openAs, scenario, state } from "./helpers"

test.describe("live dashboard", () => {
  test("risk meter and alert feed follow injected signals and events", async ({ page, request }) => {
    await openAs(page, "streamer", "/live")
    const meter = page.getByTestId("risk-meter")
    await expect(meter).toHaveAttribute("data-level", "green", { timeout: 10_000 })
    await scenario(request, "risk_meter", { level: "red", score: 0.92 })
    await expect(meter).toHaveAttribute("data-level", "red")
    const m = meter.getByRole("meter")
    await expect(m).toHaveAttribute("aria-valuenow", /\d/)
    await expect(m).toHaveAttribute("aria-valuemin", "0")
    await expect(m).toHaveAttribute("aria-valuemax", /\d+/)
    await scenario(request, "safety", { n: 3 })
    await expect(page.getByTestId("alert-feed").getByRole("listitem")).toHaveCount(3, { timeout: 8_000 })
    await expect(page.getByTestId("alert-feed")).not.toContainText(/data:image|<img/)
    expect(state().api).toBeTruthy()
  })

  test("captions control: toggle and language reach /api/config; caption lines appear", async ({ page, request }) => {
    await openAs(page, "streamer", "/live")
    const box = page.getByTestId("captions-control")
    await expect(box).toBeVisible()
    const sw = box.getByRole("switch")
    await expect(sw).toBeVisible()
    const was = (await sw.getAttribute("aria-checked")) === "true"
    const patch = page.waitForResponse((r) => r.url().endsWith("/api/config") && r.request().method() === "PATCH")
    await sw.click()
    expect((await patch).status()).toBe(200)
    expect((await getJson(request, "/api/config")).body.captions.enabled).toBe(!was)
    if (was) await sw.click() // end with captions on so the preview shows lines
    await expect(sw).toHaveAttribute("aria-checked", "true")
    const select = box.getByRole("combobox")
    const options = await select.locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value))
    const other = options.find((o) => o !== "hi") ?? options[0]
    const p2 = page.waitForResponse((r) => r.url().endsWith("/api/config") && r.request().method() === "PATCH")
    await select.selectOption(other)
    expect((await p2).status()).toBe(200)
    expect((await getJson(request, "/api/config")).body.captions.language).toBe(other)
    await scenario(request, "caption", { lang: other, text: "SPECIMEN caption line" })
    await expect(page.getByTestId("caption-lines")).toContainText("SPECIMEN caption line")
  })

  test("chat overlay shows allowed text and a hidden count; hidden text never reaches the streamer", async ({ page, request }) => {
    const frames: string[] = []
    page.on("websocket", (ws) => ws.on("framereceived", (f) => frames.push(String(f.payload))))
    const bodies: string[] = []
    page.on("response", async (r) => {
      if (r.url().startsWith(state().api)) bodies.push(await r.text().catch(() => ""))
    })
    await openAs(page, "streamer", "/live")
    await expect(page.getByTestId("chat-overlay")).toBeVisible()
    await scenario(request, "chat_allowed", { id: "ok1", text: "namaste doston" })
    await scenario(request, "chat_hidden", { id: "bad1", text: `${state().mark}-one` })
    await scenario(request, "chat_hidden", { id: "bad2", text: `${state().mark}-two` })
    await expect(page.getByTestId("chat-overlay")).toContainText("namaste doston")
    await expect(page.getByTestId("hidden-count")).toContainText(/[1-9]\d* hidden/)
    expect(await page.content()).not.toContain(state().mark)
    expect(frames.join("\n")).not.toContain(state().mark)
    expect(bodies.join("\n")).not.toContain(state().mark)
    expect(frames.join("\n")).toContain("namaste doston")
  })
})
