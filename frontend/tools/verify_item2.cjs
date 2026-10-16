// Item 2 verification: drive the UI, log in, confirm the JWT is wired.
//
// Usage: node tools/verify_item2.cjs
//
// We use puppeteer-core + Edge. The script:
//   1. opens http://127.0.0.1:5174 — the LoginPage renders
//   2. fills the form and submits
//   3. captures a screenshot of the post-login shell
//   4. extracts the in-memory token by reading what the JS sets
//      in module-scope `setAuthToken` (via a window-injected hook)
//   5. hits a protected API with the token -> 200
//   6. logs out -> next protected API call -> 401
//
// Vite is running on 127.0.0.1:5174 (HMR has already picked up our changes).
// API is running on 127.0.0.1:8765 with BOOTSTRAP_ADMIN_USERNAME=root.

const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const URL = "http://127.0.0.1:5174";
const API = "http://127.0.0.1:8765";
const EXEC = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const OUT = path.resolve(__dirname, "..", "..", "docs", "screenshots");
fs.mkdirSync(OUT, { recursive: true });

const results = [];
function record(name, ok, msg) {
  results.push({ name, ok, msg });
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}: ${msg}`);
}

async function shot(page, name) {
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, fullPage: true });
  console.log("  screenshot ->", file);
}

async function postJson(url, body, token) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const r = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const text = await r.text();
  return { status: r.status, body: text };
}

async function getJson(url, token) {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  const r = await fetch(url, { headers });
  const text = await r.text();
  return { status: r.status, body: text };
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EXEC,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });

  let capturedToken = null;
  await page.exposeFunction("__captureToken", (t) => {
    capturedToken = t;
  });

  try {
    // Patch fetch from the page to capture the Authorization header
    // whenever the frontend sends a request. We use this to recover
    // the in-memory JWT for the curl-style verification below.
    await page.evaluateOnNewDocument(() => {
      const orig = window.fetch;
      window.fetch = function patched(...args) {
        try {
          const req = args[0];
          const init = args[1] || {};
          const headers = init.headers || {};
          const auth =
            headers.authorization ||
            (headers instanceof Headers ? headers.get("authorization") : null);
          if (auth && auth.startsWith("Bearer ")) {
            window.__captureToken(auth.slice(7));
          }
        } catch (e) {
          // ignore
        }
        return orig.apply(this, args);
      };
    });

    // 1. open the page -> LoginPage should render.
    await page.goto(URL, { waitUntil: "networkidle0" });
    await new Promise((r) => setTimeout(r, 1500));

    const hasLoginForm = await page.evaluate(() => {
      return Boolean(document.querySelector("input[type=password]"));
    });
    record("1. UI renders LoginPage when anonymous", hasLoginForm, hasLoginForm ? "password input found" : "no password input");

    await shot(page, "item2_01_login.png");

    // 2. log in. Use page.type to drive React's controlled inputs.
    const inputs = await page.$$("input");
    if (inputs.length < 2) {
      record("2. LoginPage submits and shell renders", false, `found only ${inputs.length} inputs`);
    } else {
      await inputs[0].click({ clickCount: 3 });
      await inputs[0].type("root");
      await inputs[1].click({ clickCount: 3 });
      await inputs[1].type(process.env.BOOTSTRAP_ADMIN_PASSWORD || "CHANGE_ME");
      // Submit.
      await page.click("button[type=submit]");
      // Wait for the page to reload (we do window.location.assign on success).
      await new Promise((r) => setTimeout(r, 5000));
    }

    // 3. confirm we are on the authenticated shell.
    const hasSignOut = await page.evaluate(() => {
      return Array.from(document.querySelectorAll("button")).some(
        (b) => (b.textContent || "").trim() === "Sign out",
      );
    });
    record("2. LoginPage submits and shell renders", hasSignOut, hasSignOut ? "Sign out button visible" : "still on login page");

    await shot(page, "item2_02_post_login.png");

    if (!hasSignOut) {
      console.log("login did not succeed; aborting");
      await browser.close();
      process.exit(1);
    }

    // 4. capture the JWT (already captured by the patched fetch).
    // Trigger a fetch by clicking the live "Test connection" button.
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll("button")).find(
        (b) => (b.textContent || "").trim() === "Test connection",
      );
      if (btn) btn.click();
    });
    await new Promise((r) => setTimeout(r, 2500));
    record("3. Frontend sends Authorization: Bearer header on /discovery/test-connection", Boolean(capturedToken), capturedToken ? `token len=${capturedToken.length}` : "no token captured");

    // 5. use the captured token to hit a protected endpoint via fetch().
    if (capturedToken) {
      const r = await getJson(`${API}/users/me`, capturedToken);
      let me = null;
      try { me = JSON.parse(r.body); } catch (e) {}
      if (r.status === 200 && me && me.username === "root") {
        record("4. /users/me with frontend's JWT returns 200 + root", true, `id=${me.id}, role=${me.role}`);
      } else {
        record("4. /users/me with frontend's JWT returns 200 + root", false, `status=${r.status}, body=${r.body.slice(0, 200)}`);
      }
    } else {
      record("4. /users/me with frontend's JWT returns 200 + root", false, "no token captured");
    }

    // 6. verify admin-only createUser works.
    if (capturedToken) {
      const r = await postJson(
        `${API}/users`,
        { username: "item2user", password: "item2user-pwd-12345", role: "user" },
        capturedToken,
      );
      if (r.status === 201 || r.status === 409) {
        record("5. POST /users with admin JWT works", true, `status=${r.status}`);
      } else {
        record("5. POST /users with admin JWT works", false, `status=${r.status}, body=${r.body.slice(0, 200)}`);
      }
    }

    // 7. log out -> protected endpoint should 401 (or the page should
    //    bounce to login on next API call).
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll("button")).find(
        (b) => (b.textContent || "").trim() === "Sign out",
      );
      if (btn) btn.click();
    });
    await new Promise((r) => setTimeout(r, 1500));

    const hasPasswordAgain = await page.evaluate(() => {
      return Boolean(document.querySelector("input[type=password]"));
    });
    record("6. Sign out returns to LoginPage", hasPasswordAgain, hasPasswordAgain ? "password input visible again" : "still on shell");

    await shot(page, "item2_03_post_logout.png");

    // 8. an authenticated-only call should 401 with no token.
    const r = await getJson(`${API}/users/me`, null);
    if (r.status === 401) {
      record("7. /users/me without token returns 401", true, "status=401");
    } else {
      record("7. /users/me without token returns 401", false, `status=${r.status}`);
    }
  } finally {
    await browser.close();
  }

  console.log("");
  console.log("=========================================================");
  const passed = results.filter((r) => r.ok).length;
  console.log(`SUMMARY: ${passed}/${results.length} checks passed`);
  console.log("=========================================================");
  for (const r of results) {
    console.log(`  [${r.ok ? "PASS" : "FAIL"}] ${r.name}: ${r.msg}`);
  }
  process.exit(passed === results.length ? 0 : 1);
})().catch((err) => {
  console.error("error:", err);
  process.exit(1);
});
