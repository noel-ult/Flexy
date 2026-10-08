import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";

const fixtures = path.resolve(__dirname, "../../fixtures");

async function analyze(page: Page, filename: string) {
  await page.goto("/");
  await expect(page).toHaveTitle("Flexy — Linux package preparation");
  await expect(page.getByRole("heading", { name: /Turn a supported Debian package/ })).toBeVisible();
  await page.getByLabel("Choose a .deb package").setInputFiles(path.join(fixtures, filename));
  const received = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/v1/jobs" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Analyze package", exact: true }).click();
  const response = await received;
  if (response.status() !== 202) {
    // Error payloads contain no capabilities; expose the server's readable
    // explanation so CI failures do not hide behind a disabled-button timeout.
    throw new Error(`Upload HTTP ${response.status()}: ${await response.text()}`);
  }
}

async function report(page: Page) {
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download report", exact: true }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("compatibility-report.json");
  const file = await download.path();
  expect(file).not.toBeNull();
  // A consumed download token must not grant repeated artifact access.
  expect((await page.request.get(download.url())).status()).toBe(404);
  return JSON.parse(await readFile(file!, "utf8"));
}

test("real supported upload, asynchronous analysis, scoped report, and honest build limitation", async ({ page }) => {
  const apiRequests: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/v1/")) apiRequests.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await analyze(page, "flexy-demo_1.0.0_amd64.deb");
  await expect(page.getByRole("button", { name: "Start isolated build" })).toBeEnabled();
  const jobUrl = apiRequests.find((url) => /\/v1\/jobs\/[^/]+$/.test(new URL(url).pathname));
  expect(jobUrl).toBeDefined();
  expect((await page.request.get(jobUrl!)).status()).toBe(404);
  await expect(page.getByText("amd64", { exact: true })).toBeVisible();
  await expect(page.getByText("Maps to glibc", { exact: true })).toBeVisible();
  const analysis = await report(page);
  expect(analysis.compatibility.supported).toBe(true);
  expect(analysis.analysis.package.name).toBe("flexy-demo");
  expect(analysis.verification.packageCreation.state).toBe("not_run");
  await page.getByRole("button", { name: "Start isolated build" }).click();
  await expect(page.getByText("Build environment unavailable", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Arch package" })).toBeDisabled();
  const unavailable = await report(page);
  expect(unavailable.error.code).toBe("build_environment_unavailable");
  for (const check of Object.values(unavailable.verification) as { state: string }[]) {
    expect(check.state).toBe("not_run");
  }
  const origin = new URL(page.url()).origin;
  expect(apiRequests.length).toBeGreaterThan(4);
  expect(apiRequests.every((url) => new URL(url).origin === origin)).toBe(true);
  expect(errors).toEqual([]);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("unsupported dependency and maintainer script cannot start a build", async ({ page }) => {
  await analyze(page, "unsupported-demo_1.0.0_amd64.deb");
  await expect(page.getByRole("button", { name: "Download report" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Start isolated build" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Download Arch package" })).toBeDisabled();
  const result = await report(page);
  expect(result.compatibility.supported).toBe(false);
  expect(result.compatibility.blockers.length).toBeGreaterThan(0);
  expect(result.analysis.package.name).toBe("unsupported-demo");
  await expect(page.getByRole("alert")).toHaveCount(0);
});
