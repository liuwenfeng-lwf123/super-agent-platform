import { test, expect } from "@playwright/test";

const API = "http://localhost:8001/api";

// ── Backend API Tests ──────────────────────────────────────────────

test.describe("Token Budget API", () => {
  test("GET /features/token-budget returns features", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.features.length).toBeGreaterThan(0);
    expect(data).toHaveProperty("active_est_tokens");
    expect(data).toHaveProperty("total_est_tokens");
  });

  test("GET /features/token-budget/daily returns usage", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget/daily`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("date");
    expect(data).toHaveProperty("total_tokens");
    expect(data).toHaveProperty("budget");
  });

  test("GET /features/token-budget/presets returns preset list", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget/presets`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Object.keys(data.presets)).toEqual(expect.arrayContaining(["minimal", "standard", "full"]));
    expect(data).toHaveProperty("active_preset");
  });

  test("GET /features/token-budget/history returns days", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget/history?days=3`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data.days)).toBeTruthy();
  });

  test("GET /features/token-budget/cache-stats returns stats", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget/cache-stats`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("cache_hit_rate");
    expect(data).toHaveProperty("saved_usd");
    expect(data).toHaveProperty("total_records");
  });

  test("GET /features/token-budget/model-breakdown returns models", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget/model-breakdown`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(Array.isArray(data.models)).toBeTruthy();
    if (data.models.length > 0) {
      expect(data.models[0]).toHaveProperty("model");
      expect(data.models[0]).toHaveProperty("cost_usd");
      // Sorted by cost desc
      for (let i = 1; i < data.models.length; i++) {
        expect(data.models[i - 1].cost_usd).toBeGreaterThanOrEqual(data.models[i].cost_usd);
      }
    }
  });

  test("GET /features/token-budget/export returns CSV", async ({ request }) => {
    const res = await request.get(`${API}/features/token-budget/export?month=2026-04`);
    expect(res.ok()).toBeTruthy();
    const ct = res.headers()["content-type"] || "";
    expect(ct).toContain("text/csv");
    const text = await res.text();
    expect(text).toContain("日期");
  });

  test("POST toggle feature", async ({ request }) => {
    // Toggle off
    const off = await request.post(`${API}/features/token-budget/toggle`, {
      data: { feature_id: "prompt_suggestions", enable: false },
    });
    expect(off.ok()).toBeTruthy();
    // Toggle back on
    const on = await request.post(`${API}/features/token-budget/toggle`, {
      data: { feature_id: "prompt_suggestions", enable: true },
    });
    expect(on.ok()).toBeTruthy();
  });

  test("POST apply preset full", async ({ request }) => {
    const res = await request.post(`${API}/features/token-budget/preset/full`);
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.message).toContain("全功能");
  });
});

// ── Frontend UI Tests ──────────────────────────────────────────────

test.describe("Token Budget Settings UI", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Open settings
    await page.locator("[class*=settings]").first().click();
    await page.waitForTimeout(1000);
    // Click Token 预算 tab
    await page.locator("button", { hasText: "Token 预算" }).click();
    await page.waitForTimeout(1500);
  });

  test("renders Token Budget heading", async ({ page }) => {
    await expect(page.locator("h2", { hasText: "Token 预算控制" })).toBeVisible();
  });

  test("shows daily usage card", async ({ page }) => {
    await expect(page.locator("text=今日用量")).toBeVisible();
    await expect(page.locator("text=次请求").first()).toBeVisible();
  });

  test("shows 7-day trend chart", async ({ page }) => {
    await expect(page.locator("text=7 天趋势")).toBeVisible();
    // Toggle to 费用
    await page.locator("button", { hasText: "费用" }).click();
    await page.waitForTimeout(500);
  });

  test("shows budget input", async ({ page }) => {
    await expect(page.locator("input[placeholder*=每日上限]")).toBeVisible();
  });

  test("shows cache stats section", async ({ page }) => {
    const content = page.locator(".overflow-y-auto");
    await content.evaluate((el) => el.scrollBy(0, 500));
    await page.waitForTimeout(500);
    await expect(page.locator("text=Prompt Cache 统计")).toBeVisible();
  });

  test("shows model breakdown chart", async ({ page }) => {
    const content = page.locator(".overflow-y-auto");
    await content.evaluate((el) => el.scrollBy(0, 800));
    await page.waitForTimeout(500);
    await expect(page.locator("text=模型费用排行")).toBeVisible();
  });

  test("shows CSV export button", async ({ page }) => {
    const content = page.locator(".overflow-y-auto");
    await content.evaluate((el) => el.scrollBy(0, 1200));
    await page.waitForTimeout(500);
    await expect(page.locator("text=导出本月 CSV 报告")).toBeVisible();
  });

  test("shows preset modes", async ({ page }) => {
    const content = page.locator(".overflow-y-auto");
    await content.evaluate((el) => el.scrollBy(0, 1200));
    await page.waitForTimeout(500);
    await expect(page.locator("text=快捷模式")).toBeVisible();
    await expect(page.locator("text=极省模式")).toBeVisible();
    await expect(page.locator("text=标准模式")).toBeVisible();
    await expect(page.locator("text=全功能模式")).toBeVisible();
  });

  test("shows feature toggles", async ({ page }) => {
    const content = page.locator(".overflow-y-auto");
    await content.evaluate((el) => el.scrollBy(0, 1800));
    await page.waitForTimeout(500);
    await expect(page.locator("text=逐项控制")).toBeVisible();
  });
});

// ── Chat Page Tests ────────────────────────────────────────────────

test.describe("Chat Page", () => {
  test("loads without errors", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "天工流" })).toBeVisible();
  });

  test("shows mode selector", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("button", { name: "极速", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "标准", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "专业", exact: true })).toBeVisible();
  });

  test("has chat input", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("textarea")).toBeVisible();
  });
});
