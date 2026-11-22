import { expect, test, type Page } from "@playwright/test";

const SHOTS = process.env.E2E_SHOTS ?? "e2e-shots";

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon|Download the React DevTools/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
  return errors;
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle", { timeout: 120_000 });
  await expect(page.locator(".animate-spin").first()).toBeHidden({ timeout: 30_000 }).catch(() => undefined);
}

test("visitor demo: every screen renders without errors", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /From invoice photo to protected tax credit/ })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/00-landing.png`, fullPage: true });
  await page.getByRole("button", { name: /Try the live demo/ }).click();
  await page.waitForURL("**/dashboard", { timeout: process.env.E2E_BASE_URL ? 600_000 : 180_000 });
  await settle(page);
  await expect(page.getByText("Firm overview")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-dashboard.png`, fullPage: true });

  const pages: [string, string, RegExp][] = [
    ["/inbox", "02-inbox", /Documents/],
    ["/invoices", "03-register", /Purchase register/],
    ["/reconciliation", "04-reconciliation", /Matched ITC/],
    ["/ims", "05-ims", /Recommended accept/],
    ["/risk", "06-risk", /ITC at risk/],
    ["/table4", "07-table4", /Net ITC to claim/],
    ["/payables", "08-payables", /Payables/],
    ["/suppliers", "09-suppliers", /Suppliers/],
    ["/intelligence", "10-intelligence", /Circular trading/],
    ["/clients", "11-clients", /Clients/],
    ["/settings", "12-settings", /Team/],
    ["/close", "16-close", /Monthly close/],
    ["/rules", "17-rules", /GST_RATE_SLABS/],
    ["/notices", "18-notices", /Notices/],
  ];
  for (const [path, name, marker] of pages) {
    await page.goto(path);
    await settle(page);
    await expect(page.getByText(marker).first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
  }

  // Reconciliation: open the first problem row's explanation.
  await page.goto("/reconciliation");
  await settle(page);
  await page.getByRole("button", { name: /Missing in 2B/ }).click();
  await page.locator("tbody tr").first().click();
  await expect(page.getByText(/Why Lekha says/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/13-recon-explained.png`, fullPage: false });

  // Supplier detail + draft a follow-up in Telugu.
  await page.goto("/suppliers");
  await settle(page);
  await page.getByRole("link", { name: "Open" }).first().click();
  await settle(page);
  await page.screenshot({ path: `${SHOTS}/14-supplier.png`, fullPage: true });

  // Review workstation: the queue opens the first invoice; selecting a field highlights its evidence.
  await page.goto("/review");
  await page.waitForURL(/\/review\/[0-9a-f-]+$/, { timeout: 60_000 });
  await settle(page);
  await page.locator("#f-number").click();
  await page.screenshot({ path: `${SHOTS}/15-review.png`, fullPage: false });

  // Notice: record a DRC-01C and get the defence pack.
  await page.goto("/notices");
  await settle(page);
  await page.getByRole("button", { name: "Record a notice" }).click();
  await page.getByLabel(/Difference amount/).fill("125000");
  await page.getByRole("button", { name: "Build defence pack" }).click();
  await expect(page.getByText(/Defence pack v1/)).toBeVisible({ timeout: 90_000 });
  await page.screenshot({ path: `${SHOTS}/19-notice-pack.png`, fullPage: true });

  // Copilot answers a question from the data (live AI if a key is configured, offline rules otherwise).
  await page.goto("/dashboard");
  await settle(page);
  await page.getByRole("button", { name: "Ask Lekha" }).click();
  await page.getByRole("button", { name: "What deadlines are coming up?" }).click();
  await expect(page.getByText(/Looked up:/)).toBeVisible({ timeout: 120_000 });
  await page.screenshot({ path: `${SHOTS}/21-copilot.png`, fullPage: false });

  expect(errors, errors.join("\n")).toEqual([]);
});

test("mobile layout", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.screenshot({ path: `${SHOTS}/20-mobile-login.png`, fullPage: true });
  await ctx.close();
});
