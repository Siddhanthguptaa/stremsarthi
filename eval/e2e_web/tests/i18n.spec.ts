import { expect, test } from "@playwright/test"
import { openAs } from "./helpers"

test("EN/HI switch changes the headings and nav, sets lang, and persists across a reload", async ({ page }) => {
  await openAs(page, "streamer", "/controls")
  await expect(page.getByRole("heading", { level: 1, name: "Controls" })).toBeVisible()
  await page.getByRole("group", { name: "Language" }).getByRole("button", { name: "हिं" }).click()
  await expect(page.getByRole("navigation").getByRole("link", { name: "नियंत्रण" })).toBeVisible()
  await expect(page.getByRole("heading", { level: 1, name: "Controls" })).toHaveCount(0)
  await expect(page.locator("html")).toHaveAttribute("lang", "hi")
  await page.reload()
  await expect(page.getByRole("navigation").getByRole("link", { name: "नियंत्रण" })).toBeVisible()
  await page.getByRole("button", { name: "EN" }).click()
  await expect(page.getByRole("heading", { level: 1, name: "Controls" })).toBeVisible()
  await expect(page.locator("html")).toHaveAttribute("lang", "en")
})

test("the Hindi hold label is not truncated on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 })
  await openAs(page, "streamer", "/live")
  await page.getByRole("button", { name: "हिं" }).click()
  const btn = page.locator("button[data-phase]")
  await expect(btn).toContainText("रोकें")
  const clipped = await btn.evaluate((el) => el.scrollWidth > el.clientWidth + 1)
  expect(clipped).toBe(false)
  await page.getByRole("button", { name: "EN" }).click()
})
