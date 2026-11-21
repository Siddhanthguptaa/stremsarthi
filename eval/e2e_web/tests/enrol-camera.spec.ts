import { expect, test } from "@playwright/test"
import { getJson, openAs, state } from "./helpers"

test("fake camera: scan adds a face to the list and delete removes it; no stored thumbnails", async ({ page, request }) => {
  await openAs(page, "streamer", "/enrol")
  await page.getByLabel("Name or label").fill("SPECIMEN tester")
  await page.getByRole("button", { name: "Start camera" }).click()
  const scan = page.getByRole("button", { name: "Scan face" })
  await expect(scan).toBeVisible({ timeout: 15_000 })
  const post = page.waitForResponse((r) => r.url().endsWith("/api/enrol") && r.request().method() === "POST", { timeout: 30_000 })
  await scan.click()
  const resp = await post
  expect(resp.status()).toBe(201)
  await expect(page.getByText("SPECIMEN tester").first()).toBeVisible()
  expect(await page.locator("img").count()).toBe(0) // thumbnails are not kept (spec 5.1)
  const stored = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }]))
  expect(stored).not.toMatch(/data:image|base64/)
  const list = await getJson(request, "/api/enrol")
  expect(list.body.faces).toHaveLength(1)
  await page.getByRole("button", { name: /Remove: SPECIMEN tester/ }).click()
  await expect(page.getByText("Nobody is enrolled yet")).toBeVisible()
  expect((await getJson(request, "/api/enrol")).body.faces).toHaveLength(0)
  expect(state().api).toBeTruthy()
})
