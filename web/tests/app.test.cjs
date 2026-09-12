const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../js/app.js"), "utf8");

class Element {
  constructor() {
    this.children = [];
    this.listeners = {};
    this.classList = { add() {}, remove() {} };
    this.disabled = false;
  }
  set innerHTML(value) {
    this.html = value;
    this.children = [];
  }
  get innerHTML() { return this.html || ""; }
  setAttribute() {}
  querySelector() { return new Element(); }
  appendChild(child) { this.children.push(child); }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  click() { if (!this.disabled) this.listeners.click(); }
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await new Promise(resolve => setImmediate(resolve));
  }
}

async function app() {
  const elements = new Map();
  const calls = [];
  const backend = { reserved: false, failRead: false, failWrite: false, sync: null };
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  vm.runInNewContext(source, {
    document: { getElementById: get, createElement: () => new Element() },
    localStorage: { getItem: () => "token", removeItem() {} },
    window: { addEventListener() {} },
    navigator: { onLine: true },
    setTimeout() {}, clearTimeout() {},
    fetch: async (url, options) => {
      calls.push([url, options.method || "GET"]);
      let data = {};
      let ok = true;
      if (url === "/api/sync" && backend.sync) await backend.sync;
      if (url.startsWith("/api/reservations/")) {
        ok = !backend.failWrite;
        if (ok) backend.reserved = options.method === "POST";
      }
      if (url === "/api/reservations") {
        if (backend.failRead) throw new Error("Read failed");
        data = [{ date: "2099-01-05", reserved: backend.reserved }];
      }
      return { ok, status: ok ? 200 : 409, json: async () => data };
    },
  });
  await settle();
  return {
    backend, calls, get,
    button: () => get("reservation-list").children[0].children[0],
  };
}

test("reserve then cancel without reloading or redundant sync", async () => {
  const ui = await app();
  ui.button().click();
  assert.equal(ui.button().disabled, true);
  await settle();
  assert.equal(ui.button().textContent, "Cancel");
  assert.equal(ui.button().disabled, false);
  ui.button().click();
  await settle();
  assert.equal(ui.button().textContent, "Reserve");
  assert.equal(ui.button().disabled, false);
  assert.equal(ui.calls.some(([url]) => url === "/api/sync"), false);
  assert.equal(ui.calls.some(([, method]) => method === "DELETE"), true);
});

test("manual refresh waits for sync before fetching and finishes rendering", async () => {
  const ui = await app();
  let finishSync;
  ui.backend.sync = new Promise(resolve => { finishSync = resolve; });
  ui.calls.length = 0;
  ui.get("btn-refresh").click();
  await settle();
  assert.deepEqual(ui.calls, [["/api/sync", "POST"]]);
  ui.backend.reserved = true;
  finishSync();
  await settle();
  assert.equal(ui.button().textContent, "Cancel");
  assert.equal(ui.calls.length, 4);
});

test("failed follow-up refresh clears loading and allows retry", async () => {
  const ui = await app();
  ui.backend.failRead = true;
  ui.button().click();
  await settle();
  assert.equal(ui.button().disabled, false);
  assert.equal(ui.button().innerHTML.includes("spin"), false);
  assert.equal(ui.get("status-line").textContent, "Update failed");
  ui.backend.failRead = false;
  ui.button().click();
  await settle();
  assert.equal(ui.button().textContent, "Cancel");
});

test("rejected mutation clears the busy flag", async () => {
  const ui = await app();
  ui.backend.failWrite = true;
  ui.button().click();
  await settle();
  assert.equal(ui.button().disabled, false);
  assert.equal(ui.button().textContent, "Reserve");
  ui.backend.failWrite = false;
  ui.button().click();
  await settle();
  assert.equal(ui.button().textContent, "Cancel");
});
