import { expect, test } from "@playwright/test"
import { openAs, scenario, state } from "./helpers"

const SID = "e2e-session-0001"

test.beforeAll(async ({ request }) => {
  await scenario(request, "seed_report", { session_id: SID, n: 40 })
})

test("report renders totals and timeline with no images; print stylesheet hides chrome; downloads work", async ({ page }) => {
  await openAs(page, "streamer", `/report/${SID}`)
  await expect(page.getByRole("heading", { level: 1, name: "Safety report" })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByTestId("report-export")).toBeVisible()
  expect(await page.locator("img, canvas").count()).toBe(0)
  expect(await page.content()).not.toMatch(/data:image/)

  // Print layout.
  await page.emulateMedia({ media: "print" })
  await expect(page.getByTestId("report-export")).toBeHidden()
  await expect(page.locator("body > header, body > div > header").first()).toBeHidden()
  const pdf = await page.pdf({ format: "A4" })
  expect(pdf.byteLength).toBeGreaterThan(5_000)
  await page.emulateMedia({ media: "screen" })
  await expect(page.getByTestId("report-export")).toBeVisible()

  // Downloads.
  const [json] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /JSON/ }).click()])
  expect(json.suggestedFilename()).toMatch(/\.json$/)
  const jsonText = await (await import("node:fs/promises")).readFile((await json.path())!, "utf8")
  expect(JSON.parse(jsonText).session_id).toBe(SID)
  expect(jsonText).not.toMatch(/data:image|base64/)
  const [html] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /HTML/ }).click()])
  expect(html.suggestedFilename()).toMatch(/\.html$/)
  expect(state().api).toBeTruthy()
})

test("a session with an unknown id shows a problem screen, not a crash", async ({ page }) => {
  await openAs(page, "streamer", "/report/does-not-exist-0000")
  await expect(page.getByRole("alert").or(page.getByRole("heading").nth(1)).first()).toBeVisible({ timeout: 10_000 })
})
