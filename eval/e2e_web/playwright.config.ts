// Stream Sarthi T6.5 browser tests. The gate (scripts/gate_phase6.sh) starts the API (eval/e2e_web/serve_api.py)
// and `next start`, writes the state file (URLs, tokens) and runs this config. One worker: the API holds one hold
// state and one mod queue, so the specs share it in a fixed order.
import { defineConfig } from "@playwright/test"

const fakeCamera = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]

export default defineConfig({
  testDir: "./tests",
  outputDir: process.env.E2E_OUT ?? "/tmp/ss-e2e-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  reporter: [["list"], ["json", { outputFile: process.env.E2E_JSON ?? "/tmp/ss-e2e-report.json" }]],
  use: {
    viewport: { width: 1280, height: 720 },
    screenshot: "off",
    trace: "off",
    actionTimeout: 8_000,
  },
  projects: [
    { name: "main", testIgnore: /enrol-(camera|denied)/ },
    // No fake-UI flag: the permission prompt is refused, as for a user who clicked "Block".
    { name: "nocamera", testMatch: /enrol-denied/ },
    {
      name: "fakecamera",
      testMatch: /enrol-camera/,
      use: { launchOptions: { args: fakeCamera }, permissions: ["camera"] },
    },
  ],
})
