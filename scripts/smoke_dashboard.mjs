import { chromium } from "playwright";
import fs from "node:fs";

const url = process.env.DASHBOARD_URL || "https://saeedshiryan-dev.github.io/ayar-monitor/";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));

try {
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
  if (!response || !response.ok()) {
    throw new Error(`Dashboard HTTP response: ${response?.status() ?? "no response"}`);
  }

  await page.waitForFunction(() => {
    const price = document.querySelector("#price")?.textContent?.trim();
    const history = document.querySelector("#fundHistoryCount")?.textContent?.trim();
    const portfolio = document.querySelector("#codalPortfolioStatus")?.textContent?.trim();
    return price && price !== "—" && history && history !== "—" &&
      portfolio && !portfolio.includes("در انتظار");
  }, { timeout: 120000 });

  const initial = await page.evaluate(() => ({
    title: document.title,
    price: document.querySelector("#price")?.textContent?.trim(),
    rsi: document.querySelector("#rsi")?.textContent?.trim(),
    historyCount: document.querySelector("#fundHistoryCount")?.textContent?.trim(),
    portfolioStatus: document.querySelector("#codalPortfolioStatus")?.textContent?.trim(),
    portfolioInfo: document.querySelector("#codalPortfolioInfo")?.textContent?.trim(),
    hasChart: typeof Chart !== "undefined" &&
      Boolean(Chart.getChart(document.querySelector("#priceChart"))),
    hasNavPanel: Boolean(document.querySelector("#navRedemption")),
  }));

  await page.locator('.tf[data-tf="4h"]').click();
  const fourHourActive = await page.locator('.tf[data-tf="4h"]').evaluate(el => el.classList.contains("active"));
  await page.screenshot({ path: "artifacts/ayar-dashboard.png", fullPage: true });

  if (!initial.title.includes("داشبورد")) throw new Error("Unexpected page title");
  if (!initial.hasNavPanel) throw new Error("NAV panel did not render");
  if (!fourHourActive) throw new Error("4-hour timeframe control did not respond");
  if (pageErrors.length) throw new Error("Browser JavaScript errors: " + pageErrors.join(" | "));

  console.log(JSON.stringify({ ok: true, url, initial, fourHourActive, pageErrors }, null, 2));
} finally {
  await browser.close();
}
