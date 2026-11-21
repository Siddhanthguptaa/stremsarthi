import { readFileSync, appendFileSync } from "node:fs"
import { expect, type Page, type APIRequestContext } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"

export interface State {
  api: string
  web: string
  key: string
  streamer: string
  moderator: string
  fake_yt_key: string
  mark: string
}

let cached: State | null = null
export function state(): State {
  if (!cached) cached = JSON.parse(readFileSync(process.env.E2E_STATE ?? "/tmp/ss-e2e-state.json", "utf8")) as State
  return cached
}

export const bearer = (role: "streamer" | "moderator") => ({ Authorization: `Bearer ${state()[role]}` })

/** Inject what an engine or chat process would publish (see eval/e2e_web/serve_api.py). */
export async function scenario(request: APIRequestContext, name: string, args: Record<string, unknown> = {}) {
  const r = await request.post(`${state().api}/__e2e/scenario`, { headers: { "X-E2E-Key": state().key }, data: { name, args } })
  expect(r.status(), `scenario ${name}`).toBe(200)
  return (await r.json()) as Record<string, unknown>
}

/** Open a route the way the console link does: token in the URL fragment. */
export async function openAs(page: Page, role: "streamer" | "moderator", path: string) {
  await page.goto(`${state().web}${path}#token=${state()[role]}`)
}

export async function apiHold(request: APIRequestContext, active: boolean) {
  return request.post(`${state().api}/api/hold`, {
    headers: bearer("streamer"),
    data: active ? { active: true, source: "api" } : { active: false, release_all: true },
  })
}

export async function getJson(request: APIRequestContext, path: string, role: "streamer" | "moderator" = "streamer") {
  const r = await request.get(`${state().api}${path}`, { headers: bearer(role) })
  return { status: r.status(), body: r.status() === 200 ? await r.json() : null }
}

export const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))

export interface AxeFinding {
  route: string
  viewport: string
  id: string
  impact: string
  help: string
  targets: string[]
}

/** Scan a page and append the serious and critical findings to the JSON-lines file the gate reads. */
export async function axeScan(page: Page, route: string): Promise<AxeFinding[]> {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze()
  const viewport = `${page.viewportSize()?.width ?? "?"}px`
  const findings = result.violations.map((v) => ({
    route,
    viewport,
    id: v.id,
    impact: v.impact ?? "unknown",
    help: v.help,
    targets: v.nodes.slice(0, 6).map((n) => n.target.join(" ")),
  }))
  try {
    appendFileSync(process.env.E2E_AXE ?? "/tmp/ss-e2e-axe.jsonl", findings.map((f) => JSON.stringify(f)).join("\n") + (findings.length ? "\n" : ""))
  } catch {
    /* the report file is optional */
  }
  return findings.filter((f) => f.impact === "serious" || f.impact === "critical")
}

/** Wait for the page to show it is connected (the live and mod consoles open a WebSocket). */
export async function settle(page: Page) {
  await page.waitForLoadState("networkidle").catch(() => {})
}
