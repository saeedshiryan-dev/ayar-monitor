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
    const liveLast = document.querySelector("#liveLast")?.textContent?.trim();
    const liveClose = document.querySelector("#liveClose")?.textContent?.trim();
    const liveTime = document.querySelector("#liveTime")?.textContent?.trim();
    const navRedemption = document.querySelector("#navRedemption")?.textContent?.trim();
    const navSubscription = document.querySelector("#navSubscription")?.textContent?.trim();
    const navPublishedAt = document.querySelector("#navPublishedAt")?.textContent?.trim();
    const bookStatus = document.querySelector("#bookStatus")?.textContent?.trim();
    return price && price !== "—" && history && history !== "—" &&
      portfolio && !portfolio.includes("در انتظار") &&
      liveLast && liveLast !== "—" && liveClose && liveClose !== "—" &&
      liveTime && liveTime !== "—" && navRedemption && navRedemption !== "—" &&
      navSubscription && navSubscription !== "—" && navPublishedAt &&
      navPublishedAt !== "—" && bookStatus === "دریافت شد";
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
    liveLast: document.querySelector("#liveLast")?.textContent?.trim(),
    liveClose: document.querySelector("#liveClose")?.textContent?.trim(),
    liveTime: document.querySelector("#liveTime")?.textContent?.trim(),
    navRedemption: document.querySelector("#navRedemption")?.textContent?.trim(),
    navSubscription: document.querySelector("#navSubscription")?.textContent?.trim(),
    navPremium: document.querySelector("#navPremium")?.textContent?.trim(),
    navPublishedAt: document.querySelector("#navPublishedAt")?.textContent?.trim(),
    bookStatus: document.querySelector("#bookStatus")?.textContent?.trim(),
    bookUpdated: document.querySelector("#bookUpdated")?.textContent?.trim(),
    fundLatestRedemption: document.querySelector("#fundLatestRedemption")?.textContent?.trim(),
    fundLatestSubscription: document.querySelector("#fundLatestSubscription")?.textContent?.trim(),
    historyNavHeader: document.querySelector("#fundHistoryRows")?.parentElement?.querySelector("thead")?.textContent?.trim(),
    portfolioTableCount: document.querySelectorAll("#codalPortfolioTables table").length,
    portfolioAssetRows: document.querySelector("#codalPortfolioTables table tbody")?.querySelectorAll("tr").length ??
      document.querySelector("#codalPortfolioTables table")?.querySelectorAll("tr").length ?? 0,
  }));

  await page.locator('.tf[data-tf="4h"]').click();
  const fourHourActive = await page.locator('.tf[data-tf="4h"]').evaluate(el => el.classList.contains("active"));
  await page.screenshot({ path: "artifacts/ayar-dashboard.png", fullPage: true });

  if (!initial.title.includes("داشبورد")) throw new Error("Unexpected page title");
  if (!initial.hasNavPanel) throw new Error("NAV panel did not render");
  for (const key of ["liveLast", "liveClose", "liveTime", "navRedemption", "navSubscription", "navPremium", "navPublishedAt"]) {
    if (!initial[key] || initial[key] === "—") throw new Error("Live quote/NAV field did not render: " + key);
  }
  if (initial.bookStatus !== "دریافت شد") throw new Error("Order book was not loaded: " + initial.bookStatus);
  if (!initial.fundLatestRedemption || initial.fundLatestRedemption === "—" ||
      !initial.fundLatestSubscription || initial.fundLatestSubscription === "—") {
    throw new Error("Historical NAV values did not render");
  }
  if (!initial.historyNavHeader?.includes("تومان")) throw new Error("Historical NAV table unit is not explicit");
  if (initial.portfolioTableCount < 2 || initial.portfolioAssetRows < 5) {
    throw new Error("Uploaded Codal portfolio tables did not render completely");
  }
  if (!fourHourActive) throw new Error("4-hour timeframe control did not respond");

  // Verify the published page also fits a narrow mobile viewport.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForFunction(() => document.querySelector("#liveLast")?.textContent?.trim() &&
    document.querySelector("#codalPortfolioStatus")?.textContent?.trim() &&
    document.querySelector("#fundLatestRedemption")?.textContent?.trim() !== "—", { timeout: 120000 });
  const mobile = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    portfolioTables: document.querySelectorAll("#codalPortfolioTables table").length,
    noHorizontalPageOverflow: document.documentElement.scrollWidth <= window.innerWidth + 2,
  }));
  if (!mobile.noHorizontalPageOverflow) throw new Error("Mobile page has horizontal overflow: " + JSON.stringify(mobile));
  if (pageErrors.length) throw new Error("Browser JavaScript errors: " + pageErrors.join(" | "));

  console.log(JSON.stringify({ ok: true, url, initial, fourHourActive, mobile, pageErrors }, null, 2));
} finally {
  await browser.close();
}
