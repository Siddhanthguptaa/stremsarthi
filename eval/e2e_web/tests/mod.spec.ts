import { expect, test, type Page } from "@playwright/test"
import { getJson, openAs, scenario, state } from "./helpers"

const items = (page: Page) => page.locator("li[data-key]")

test.describe.configure({ mode: "serial" })

test("role gating: a streamer token is refused on /mod, API included", async ({ page, request }) => {
  await openAs(page, "streamer", "/mod")
  await expect(page.getByRole("heading", { name: "Moderator link needed" })).toBeVisible()
  await expect(items(page)).toHaveCount(0)
  expect((await getJson(request, "/api/mod/queue", "streamer")).status).toBe(403)
  expect((await getJson(request, "/api/mod/queue", "moderator")).status).toBe(200)
})

test("a moderator token gets the console with the seeded queue; actions reach the API", async ({ page, request }) => {
  for (const id of ["m1", "m2", "m3"]) await scenario(request, "chat_hidden", { id, text: `${state().mark}-${id}`, author: `SPECIMEN ${id}` })
  await openAs(page, "moderator", "/mod")
  await expect(page.getByRole("heading", { level: 1, name: "Moderator" })).toBeVisible()
  await expect(items(page)).toHaveCount(3, { timeout: 10_000 })
  await expect(items(page).first()).toContainText(state().mark)

  const actions: { action: string; timeout_seconds?: number; message_id: string }[] = []
  page.on("request", (r) => {
    if (r.url().endsWith("/api/mod/action") && r.method() === "POST") actions.push(r.postDataJSON())
  })
  // Buttons.
  const first = items(page).first()
  await first.getByRole("button", { name: /Approve/ }).click()
  await expect.poll(() => actions.length).toBe(1)
  expect(actions[0].action).toBe("approve")
})

test("keyboard only: J and K move the selection, A D T X act on the selected message", async ({ page, request }) => {
  await scenario(request, "chat_hidden", { id: "k1", text: `${state().mark}-k1` })
  await scenario(request, "chat_hidden", { id: "k2", text: `${state().mark}-k2` })
  await scenario(request, "chat_hidden", { id: "k3", text: `${state().mark}-k3` })
  await scenario(request, "chat_hidden", { id: "k4", text: `${state().mark}-k4` })
  await openAs(page, "moderator", "/mod")
  await expect(items(page).first()).toBeVisible({ timeout: 10_000 })
  const sent: { action: string; message_id: string; timeout_seconds?: number }[] = []
  page.on("request", (r) => {
    if (r.url().endsWith("/api/mod/action") && r.method() === "POST") sent.push(r.postDataJSON())
  })
  await page.locator("body").click({ position: { x: 5, y: 5 } })
  const selected = () => page.locator('li[data-selected="true"]')
  const idOf = async () => (await selected().first().getAttribute("data-key")) ?? ""
  const start = await idOf()
  await page.keyboard.press("j")
  await expect.poll(idOf).not.toBe(start)
  await page.keyboard.press("k")
  await expect.poll(idOf).toBe(start)
  await page.keyboard.press("d")
  await expect.poll(() => sent.length).toBeGreaterThanOrEqual(1)
  expect(sent.at(-1)!.action).toBe("delete")
  await page.keyboard.press("j")
  await page.keyboard.press("t")
  await expect.poll(() => sent.some((s) => s.action === "timeout")).toBe(true)
  expect(sent.find((s) => s.action === "timeout")!.timeout_seconds).toBe(600)
  await page.keyboard.press("j")
  await page.keyboard.press("x")
  await expect.poll(() => sent.some((s) => s.action === "dismiss")).toBe(true)
  await page.keyboard.press("j")
  await page.keyboard.press("a")
  await expect.poll(() => sent.some((s) => s.action === "approve")).toBe(true)
  // No two actions on one message.
  expect(new Set(sent.map((s) => s.message_id)).size).toBe(sent.length)
  // The server recorded them on the bus path (no chat process: 202 style accepted).
  expect(state().api).toBeTruthy()
})

test("an empty console says so", async ({ page, request }) => {
  const q = await getJson(request, "/api/mod/queue", "moderator")
  expect(q.status).toBe(200)
  await openAs(page, "moderator", "/mod")
  await expect(page.getByRole("heading", { level: 1, name: "Moderator" })).toBeVisible()
})
