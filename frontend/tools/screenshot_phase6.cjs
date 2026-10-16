// Take Phase 6 screenshots of the running UI.
// Usage: node tools/screenshot_phase6.cjs

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
    await shot(page, "phase6_ui_01_idle.png");

    await clickByText(page, "Test connection");
    await shot(page, "phase6_ui_02_test_connection.png");

    await clickByText(page, "List databases");
    await new Promise((r) => setTimeout(r, 600));

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
    await shot(page, "phase6_ui_03_schema_overview.png", true);

    // Click on Customers row to expand it.
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
    if (expanded) {
      await new Promise((r) => setTimeout(r, 800));
      // Click "Classify columns" button if found.
      const classified = await page.evaluate(() => {
        const all = Array.from(document.querySelectorAll("button"));
        const btn = all.find((b) => (b.textContent || "").includes("Classify columns"));
        if (btn) {
          btn.click();
          return true;
        }
        return false;
      });
      if (classified) {
        // Wait for the LLM call (~30s cold).
        await new Promise((r) => setTimeout(r, 60000));
        await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("header"));
          const target = all.find((h) =>
            (h.textContent || "").includes("dbo.Customers"),
          );
          if (target) target.scrollIntoView({ block: "start" });
        });
        await new Promise((r) => setTimeout(r, 600));
        await shot(page, "phase6_ui_04_classification_real_ollama.png", true);
      } else {
        console.log("Classify button not found; taking expanded shot anyway");
        await shot(page, "phase6_ui_04_classification_real_ollama.png", true);
      }
    }

    console.log("done");
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error("error:", err);
  process.exit(1);
});
