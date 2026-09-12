const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../sw.js"), "utf8");

function worker(fetch) {
  const listeners = {};
  const cached = { body: "old app code" };
  const writes = [];
  vm.runInNewContext(source, {
    URL, fetch,
    self: {
      location: { origin: "https://cafeteria.example" },
      addEventListener: (name, handler) => { listeners[name] = handler; },
    },
    caches: {
      match: async () => cached,
      open: async () => ({ put: async (request, response) => writes.push(response) }),
    },
  });
  return {
    cached, writes,
    load() {
      let result;
      listeners.fetch({
        request: { method: "GET", url: "https://cafeteria.example/js/app.js" },
        respondWith: promise => { result = promise; },
      });
      return result;
    },
  };
}

test("online asset loads return current code instead of stale cache", async () => {
  const latest = { ok: true, body: "new app code", clone() { return this; } };
  const sw = worker(async () => latest);
  assert.equal(await sw.load(), latest);
  assert.deepEqual(sw.writes, [latest]);
});

test("offline asset loads still fall back to cache", async () => {
  const sw = worker(async () => { throw new Error("Offline"); });
  assert.equal(await sw.load(), sw.cached);
});
