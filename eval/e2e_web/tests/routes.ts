/** Every route, with the role that opens it. The report id is seeded by report.spec.ts (or the gate before the run). */
export const ROUTES: { path: string; role: "streamer" | "moderator"; name: string }[] = [
  { path: "/", role: "streamer", name: "home" },
  { path: "/go-live", role: "streamer", name: "go-live" },
  { path: "/enrol", role: "streamer", name: "enrol" },
  { path: "/controls", role: "streamer", name: "controls" },
  { path: "/live", role: "streamer", name: "live" },
  { path: "/mod", role: "streamer", name: "mod-refused" },
  { path: "/mod", role: "moderator", name: "mod-console" },
  { path: "/report/e2e-session-0001", role: "streamer", name: "report" },
]
