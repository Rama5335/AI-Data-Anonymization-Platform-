// Take Phase 14-style screenshots of every tab.
// Usage: node tools/screenshot_tabs.cjs

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
  await page.evaluate((t) => {
    const all = Array.from(document.querySelectorAll("button, [role=button]"));
    const el = all.find((n) => (n.textContent || "").trim().startsWith(t));
    if (el) el.click();
  }, text);
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

    // Test connection, list DBs, run discovery + classify.
    await clickByText(page, "Test connection");
    await new Promise((r) => setTimeout(r, 400));
    await clickByText(page, "List databases");
    await new Promise((r) => setTimeout(r, 600));
    const sel = await page.$("select");
    if (sel) {
      const opts = await page.$$eval("select option", (els) =>
        els.map((e) => ({ value: e.value, label: e.textContent })),
      );
      const target = opts.find((o) => o.label.includes("anonymizer_app"));
      if (target) await page.select("select", target.value);
      await new Promise((r) => setTimeout(r, 400));
    }
    await clickByText(page, "Run schema discovery");
    await new Promise((r) => setTimeout(r, 1500));

    // Click "Classify columns" once on Customers to populate the
    // classification panel for the screenshot.
    await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("header"));
      const t = all.find((h) => (h.textContent || "").includes("dbo.Customers"));
      if (t) t.click();
    });
    await new Promise((r) => setTimeout(r, 700));
    await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("button"));
      const b = all.find((x) => (x.textContent || "").includes("Classify columns"));
      if (b) b.click();
    });
    // Wait for real Ollama call.
    await new Promise((r) => setTimeout(r, 60000));

    // Per-tab screenshots.
    for (const [tab, name] of [
      ["Schema & Sample", "phase14_tab_schema.png"],
      ["Approval Center", "phase14_tab_approval.png"],
      ["Backup Center", "phase14_tab_backup.png"],
      ["Execution Monitor", "phase14_tab_execution.png"],
      ["Audit Log", "phase14_tab_audit.png"],
      ["Workflow", "phase14_tab_workflow.png"],
      ["Settings", "phase14_tab_settings.png"],
    ]) {
      await page.evaluate((t) => {
        const all = Array.from(document.querySelectorAll("button"));
        const b = all.find((x) => (x.textContent || "").trim() === t);
        if (b) b.click();
      }, tab);
      await new Promise((r) => setTimeout(r, 1200));
      await shot(page, name, true);
    }
    console.log("done");
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error("error:", err);
  process.exit(1);
});
