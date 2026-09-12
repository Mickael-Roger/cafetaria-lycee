const assert = require("node:assert/strict");
const { readFile } = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { test } = require("node:test");
const { chromium } = require("playwright");

// Keep the old worker active throughout the upgrade test. A new worker alone
// cannot fix the script that the old worker already supplied to the page.
const legacyWorker = `
self.addEventListener('install', event => event.waitUntil(
  caches.open('cafetaria-cache-v1').then(cache => cache.addAll(['/', '/js/app.js', '/css/app.css']))
    .then(() => self.skipWaiting())
));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (event.request.mode === 'navigate' || new URL(event.request.url).pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => {
    const network = fetch(event.request).then(response => {
      const copy = response.clone();
      caches.open('cafetaria-cache-v1').then(cache => cache.put(event.request, copy));
      return response;
    });
    return cached || network;
  }));
});`;

async function fixture(t, options = {}) {
  const state = { legacy: !!options.legacy, reserved: false, holdReads: false };
  const sockets = new Set();
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    response.setHeader("Cache-Control", "no-store");
    if (url.pathname.startsWith("/api/")) {
      response.setHeader("Content-Type", "application/json");
      let data = {};
      if (url.pathname === "/api/me") data = { username: "test" };
      if (url.pathname === "/api/reservations") data = [{ date: "2099-01-05", reserved: state.reserved }];
      if (url.pathname === "/api/credit") data = { credit: "Solde : 42,00 EUR" };
      if (url.pathname === "/api/status") data = { last_update: new Date().toISOString() };
      if (url.pathname.startsWith("/api/reservations/")) {
        state.reserved = request.method === "POST";
        data = { status: "success", message: state.reserved ? "Reservation done" : "Reservation canceled" };
      } else if (state.holdReads && request.method === "GET") {
        return;
      }
      response.end(JSON.stringify(data));
      return;
    }
    const file = url.pathname === "/" ? "/index.html" : url.pathname;
    const types = { ".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".png": "image/png" };
    response.setHeader("Content-Type", types[path.extname(file)] || "application/json");
    try {
      let body = await readFile(path.join(__dirname, "..", file));
      if (options.legacy && file === "/sw.js") body = legacyWorker;
      if (state.legacy && file === "/index.html") body = body.toString().replace(/\?v=\d+/g, "");
      if (state.legacy && file === "/js/app.js") body = "window.legacyAppLoaded = true;";
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
  });
  server.on("connection", socket => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    for (const socket of sockets) socket.destroy();
    return new Promise(resolve => server.close(resolve));
  });
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const context = await browser.newContext(options.mobile
    ? { viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true }
    : {});
  await context.addInitScript(() => localStorage.setItem("cafetaria_token", "test"));
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  return { page, context, state, url: `http://127.0.0.1:${server.address().port}` };
}

test("upgrade bypasses an old worker's cached unversioned JavaScript", async t => {
  const { page, state, url } = await fixture(t, { legacy: true });
  await page.goto(url);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  assert.equal(await page.evaluate(() => window.legacyAppLoaded), true);
  state.legacy = false;
  await page.reload();
  await page.locator(".btn-reserve").waitFor({ timeout: 5000 });
  assert.equal(await page.evaluate(() => window.legacyAppLoaded), undefined);
  await page.locator(".btn-reserve").click();
  await page.waitForFunction(() => document.querySelector(".reservation-item.reserved .btn-cancel:not(:disabled)"));
  assert.equal(await page.locator(".reservation-item .spin").count(), 0);
});

for (const mobile of [false, true]) {
  test(`${mobile ? "mobile" : "desktop"}: reservation updates and native payment navigation`, async t => {
    const { page, context, state, url } = await fixture(t, { mobile });
    await page.goto(url);
    await page.locator(".btn-reserve").waitFor();
    await page.evaluate(() => navigator.serviceWorker.ready);
    assert.equal(await page.locator(".credit-card").innerText(), "Solde : 42,00 EUR");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    state.holdReads = true;
    await page.locator(".btn-reserve").click();
    await page.waitForFunction(() => document.querySelector(".reservation-item.reserved .btn-cancel:not(:disabled)"));
    assert.equal(await page.locator(".reservation-item .spin").count(), 0);
    assert.equal(await page.locator("#next-reservation").isVisible(), true);
    await page.locator(".btn-cancel").click();
    await page.waitForFunction(() => document.querySelector(".reservation-item.available .btn-reserve:not(:disabled)"));

    // No real payment requests and no dependence on JavaScript popup support.
    await context.route("https://webparent.paiementdp.com/**", route => route.fulfill({ body: "Payment page" }));
    await page.evaluate(() => { window.open = () => { throw new Error("window.open must not be used"); }; });
    const opened = context.waitForEvent("page");
    await page.locator("#btn-refill").click();
    const payment = await opened;
    await payment.waitForLoadState();
    assert.equal(new URL(payment.url()).pathname, "/aliAuthentification.php");
    assert.equal(new URL(payment.url()).search, "?site=aes00152");
    assert.equal(new URL(payment.url()).origin, "https://webparent.paiementdp.com");
    assert.equal(await payment.evaluate(() => window.opener), null);
    assert.equal(page.url(), url + "/");
  });
}
