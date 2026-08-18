import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: false,
    environment: "node",
    // Each test file gets a fresh module registry so env stubs don't bleed
    isolate: true,
    pool: "forks",
    include: ["src/**/*.test.ts"],
  },
});
