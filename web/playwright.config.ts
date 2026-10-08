import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "/tmp/flexy-browser-results",
  timeout: 120_000,
  expect: { timeout: 60_000 },
  workers: 1,
  retries: 0,
  use: {
    baseURL: process.env.FLEXY_E2E_URL || "http://127.0.0.1:3000",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { browserName: "chromium", viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { browserName: "chromium", viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
