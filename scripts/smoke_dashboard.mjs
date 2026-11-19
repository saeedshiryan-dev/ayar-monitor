import { chromium } from "playwright";

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

  // The dashboard must load the published fundamental dossier even when
  // market-only feeds are closed, delayed, or temporarily unavailable.
  await page.waitForFunction(() => {
    const score = document.querySelector("#fundamentalScore")?.textContent?.trim();
    const portfolioRows = document.querySelectorAll("#fundamentalPortfolioRows tr").length;
    const scoreRows = document.querySelectorAll("#fundamentalScoreRows tr").length;
    return score && score.includes("/10") && portfolioRows >= 5 && scoreRows >= 8;
  }, { timeout: 120000 });

  const initial = await page.evaluate(() => ({
    title: document.title,
    fundamentalScore: document.querySelector("#fundamentalScore")?.textContent?.trim(),
    fundamentalGoldWeight: document.querySelector("#fundamentalGoldWeight")?.textContent?.trim(),
    fundamentalOneYear: document.querySelector("#fundamentalOneYear")?.textContent?.trim(),
    portfolioRows: document.querySelectorAll("#fundamentalPortfolioRows tr").length,
    peerRows: document.querySelectorAll("#fundamentalPeers tr").length,
    scoreRows: document.querySelectorAll("#fundamentalScoreRows tr").length,
    scenarioRows: document.querySelectorAll("#fundamentalScenarioRows tr").length,
    hasChart: typeof Chart !== "undefined" &&
      Boolean(Chart.getChart(document.querySelector("#priceChart"))),
    hasNavPanel: Boolean(document.querySelector("#navRedemption")),
  }));

  await page.locator('.tf[data-tf="4h"]').click();
  const fourHourActive = await page.locator('.tf[data-tf="4h"]').evaluate(el => el.classList.contains("active"));
  await page.screenshot({ path: "artifacts/ayar-dashboard.png", fullPage: true });

  if (!initial.title.includes("داشبورد")) throw new Error("Unexpected page title");
  if (!initial.fundamentalScore.includes("8/10")) throw new Error("Fundamental scorecard did not render");
  if (initial.portfolioRows < 5 || initial.scoreRows < 8) throw new Error("Fundamental tables are incomplete");
  if (initial.peerRows < 5 || initial.scenarioRows < 5) throw new Error("Peer or scenario tables are incomplete");
  if (!initial.hasNavPanel) throw new Error("NAV panel did not render");
  if (!fourHourActive) throw new Error("4-hour timeframe control did not respond");

  // Verify the fundamental panel remains usable on a narrow mobile viewport.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForFunction(() =>
    document.querySelector("#fundamentalScore")?.textContent?.includes("/10") &&
    document.querySelectorAll("#fundamentalScoreRows tr").length >= 8,
    { timeout: 120000 }
  );
  const mobile = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    noHorizontalPageOverflow: document.documentElement.scrollWidth <= window.innerWidth + 2,
  }));
  if (!mobile.noHorizontalPageOverflow) throw new Error("Mobile page has horizontal overflow: " + JSON.stringify(mobile));
  if (pageErrors.length) throw new Error("Browser JavaScript errors: " + pageErrors.join(" | "));

  console.log(JSON.stringify({ ok: true, url, initial, fourHourActive, mobile, pageErrors }, null, 2));
} finally {
  await browser.close();
}
