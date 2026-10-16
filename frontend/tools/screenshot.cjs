// Take screenshots of the running UI to document Phase 5 sampling visually.
// Usage: node tools/screenshot.cjs

const puppeteer = require("puppeteer-core");
const path = require("path");

const URL = "http://127.0.0.1:5174";
const OUT = path.resolve(__dirname, "..", "..", "docs", "screenshots");
const EXEC = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

async function shot(page, name, full = false) {
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, fullPage: full });
  console.log("  " + name);
}

async function clickByText(page, text) {
  const el = await page.evaluateHandle((t) => {
    const all = Array.from(document.querySelectorAll("button, select, [role=button]"));
    return all.find((n) => (n.textContent || "").trim().startsWith(t));
  }, text);
  if (!el) throw new Error("button not found: " + text);
  await el.click();
  await new Promise((r) => setTimeout(r, 600));
}

async function selectOption(page, selectId, value) {
  await page.select(selectId, value);
  await new Promise((r) => setTimeout(r, 400));
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EXEC,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900, deviceScaleFactor: 1 });

  try {
    await page.goto(URL, { waitUntil: "networkidle0" });
    await new Promise((r) => setTimeout(r, 800));

    // 1. Initial state.
    await shot(page, "phase5_ui_01_idle.png");

    // 2. Test connection.
    await clickByText(page, "Test connection");
    await shot(page, "phase5_ui_02_test_connection.png");

    // 3. List databases.
    await clickByText(page, "List databases");
    await new Promise((r) => setTimeout(r, 500));
    await shot(page, "phase5_ui_03_databases.png");

    // 4. Pick anonymizer_app and run discovery.
    const dbSelect = await page.$("select");
    if (dbSelect) {
      const opts = await page.$$eval("select option", (els) =>
        els.map((e) => ({ value: e.value, label: e.textContent })),
      );
      const target = opts.find((o) => o.label.includes("anonymizer_app"));
      if (target) {
        await page.select("select", target.value);
        await new Promise((r) => setTimeout(r, 300));
      }
    }
    await clickByText(page, "Run schema discovery");
    await new Promise((r) => setTimeout(r, 1500));
    await shot(page, "phase5_ui_04_schema_overview.png");

    // 5. Expand Customers.
    const expanded = await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("header"));
      const target = all.find((h) =>
        (h.textContent || "").includes("dbo.Customers"),
      );
      if (target) {
        target.click();
        return true;
      }
      return false;
    });
    if (!expanded) throw new Error("Customers header not found");
    await new Promise((r) => setTimeout(r, 500));

    // 6. Click "Sample 5 rows" on Customers.
    const sampleClicked = await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("button"));
      const btn = all.find((b) => (b.textContent || "").includes("Sample 5 rows"));
      if (btn) {
        btn.click();
        return true;
      }
      return false;
    });
    if (!sampleClicked) throw new Error("Sample button not found");
    await new Promise((r) => setTimeout(r, 1500));
    await shot(page, "phase5_ui_05_customers_sampled.png");

    // 7. Expand CustomerNotes (LOB columns).
    const expanded2 = await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("header"));
      const target = all.find((h) =>
        (h.textContent || "").includes("dbo.CustomerNotes"),
      );
      if (target) {
        target.click();
        return true;
      }
      return false;
    });
    if (expanded2) {
      await new Promise((r) => setTimeout(r, 500));
      const sampleClicked2 = await page.evaluate(() => {
        const all = Array.from(document.querySelectorAll("button"));
        const btn = all.find(
          (b) => (b.textContent || "").includes("Sample 5 rows"),
        );
        if (btn) {
          btn.click();
          return true;
        }
        return false;
      });
      if (sampleClicked2) {
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
    // Scroll to the CustomerNotes block and capture a full-page shot.
    await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("header"));
      const target = all.find((h) =>
        (h.textContent || "").includes("dbo.CustomerNotes"),
      );
      if (target) target.scrollIntoView({ block: "start" });
    });
    await new Promise((r) => setTimeout(r, 600));
    await shot(page, "phase5_ui_06_customer_notes_truncated.png", true);

    // Also re-capture the Customers sample region with the sample panel visible.
    await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("header"));
      const target = all.find((h) =>
        (h.textContent || "").includes("dbo.Customers"),
      );
      if (target) target.scrollIntoView({ block: "start" });
    });
    await new Promise((r) => setTimeout(r, 600));
    await shot(page, "phase5_ui_05_customers_sampled.png", true);

    console.log("done");
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error("error:", err);
  process.exit(1);
});
