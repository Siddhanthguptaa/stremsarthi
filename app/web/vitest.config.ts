import path from "node:path"
import { defineConfig } from "vitest/config"

export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname) } },
  test: {
    // Logic tests run in node. Component tests opt in with `// @vitest-environment jsdom` at the top.
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup-dom.ts"],
  },
})
