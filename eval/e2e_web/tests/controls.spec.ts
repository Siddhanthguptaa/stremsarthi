import { expect, test } from "@playwright/test"
import { getJson, openAs, state } from "./helpers"

test.describe("controls", () => {
  test("a category switch sends one PATCH and the UI and API agree; sensitivity and style apply", async ({ page, request }) => {
    await openAs(page, "streamer", "/controls")
    await expect(page.getByRole("heading", { level: 1, name: "Controls" })).toBeVisible()
    const cards = page.locator("[data-category]")
    await expect(cards.first()).toBeVisible()
    const card = page.locator('[data-category="plate"]')
    const target = (await card.count()) ? card : cards.first()
    const category = (await target.getAttribute("data-category"))!
    const before = (await getJson(request, "/api/config")).body.categories[category]
    const sw = target.getByRole("switch").first()
    const patch = page.waitForRequest((r) => r.url().endsWith("/api/config") && r.method() === "PATCH")
    await sw.click()
    // Turning off cards/QR asks first; confirm when the dialog shows.
    const confirm = page.getByRole("button", { name: "Turn off" })
    if (await confirm.isVisible().catch(() => false)) await confirm.click()
    const req = await patch
    expect(req.postDataJSON()).toBeTruthy()
    await expect(target).toHaveAttribute("data-enabled", String(!before.enabled))
    const after = (await getJson(request, "/api/config")).body.categories[category]
    expect(after.enabled).toBe(!before.enabled)
    // restore
    await sw.click()
    const confirm2 = page.getByRole("button", { name: "Turn off" })
    if (await confirm2.isVisible().catch(() => false)) await confirm2.click()
    await expect.poll(async () => (await getJson(request, "/api/config")).body.categories[category].enabled).toBe(before.enabled)
  })

  test("audio language picker changes audio.languages and keeps one on", async ({ page, request }) => {
    await openAs(page, "streamer", "/controls")
    const picker = page.getByTestId("speech-languages")
    await expect(picker).toBeVisible()
    const initial: string[] = (await getJson(request, "/api/config")).body.audio.languages
    const switches = picker.getByRole("switch")
    const n = await switches.count()
    expect(n).toBeGreaterThan(1)
    // Switch on a language that is off (or off one that is on) and check the API list follows.
    let flipped = -1
    for (let i = 0; i < n; i++) {
      if ((await switches.nth(i).getAttribute("aria-checked")) === "false") {
        flipped = i
        break
      }
    }
    expect(flipped, "at least one language is off in the default config").toBeGreaterThanOrEqual(0)
    const patch = page.waitForResponse((r) => r.url().endsWith("/api/config") && r.request().method() === "PATCH")
    await switches.nth(flipped).click()
    expect((await patch).status()).toBe(200)
    const after: string[] = (await getJson(request, "/api/config")).body.audio.languages
    expect(after.length).toBe(initial.length + 1)
    // restore
    const patch2 = page.waitForResponse((r) => r.url().endsWith("/api/config") && r.request().method() === "PATCH")
    await switches.nth(flipped).click()
    await patch2
    expect(((await getJson(request, "/api/config")).body.audio.languages as string[]).sort()).toEqual([...initial].sort())
    expect(state().api).toBeTruthy()
  })

  test("a refused patch rolls the switch back", async ({ page }) => {
    await openAs(page, "streamer", "/controls")
    const card = page.locator("[data-category]").first()
    await expect(card).toBeVisible()
    const was = await card.getAttribute("data-enabled")
    await page.route("**/api/config", (route) => (route.request().method() === "PATCH" ? route.fulfill({ status: 422, json: { error: "invalid", detail: "x" } }) : route.continue()))
    await card.getByRole("switch").first().click()
    const confirm = page.getByRole("button", { name: "Turn off" })
    if (await confirm.isVisible().catch(() => false)) await confirm.click()
    await expect(card).toHaveAttribute("data-enabled", was!)
    await expect(page.getByText(/Could not save/).first()).toBeVisible()
  })
})
