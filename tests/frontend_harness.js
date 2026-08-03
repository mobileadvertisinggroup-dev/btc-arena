/* Offline front-end payload-contract harness (Ruling 011.1/011.3).
 *
 * Executes the production page's inline scripts under a permissive DOM stub
 * with a given payload and reports any JavaScript error. No network: fetch is
 * stubbed (ticker URLs fail like an offline browser; live_payload fetches are
 * served from a local file), so this proves the exact production payload
 * renders without JS errors, and that an already-"open" page picks up the
 * NEXT published payload automatically via its cache-busted poll.
 *
 * usage: node frontend_harness.js <index.html> <payloadA.js|none> [payloadB.js]
 * stdout: single JSON line {ok, boot_live_id, polled_live_id, mode, error?}
 */
'use strict';
const fs = require('fs');

const [, , indexPath, payloadA, payloadB] = process.argv;
const html = fs.readFileSync(indexPath, 'utf8');

/* ---------------- permissive DOM stub ---------------- */
function makeEl(id) {
  const el = {
    id: id || '', style: {}, dataset: {}, children: [],
    innerHTML: '', textContent: '', value: '', display: '',
    width: 800, height: 400,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, remove() {}, focus() {},
    setAttribute() {}, getAttribute() { return null; },
    insertAdjacentHTML() {},
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 800, height: 400, left: 0, top: 0 }; },
    getContext() {
      return new Proxy({}, { get: (t, k) => (k === 'canvas' ? el : () => {}) });
    },
  };
  return el;
}
const byId = new Map();
const document = {
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, makeEl(id));
    return byId.get(id);
  },
  createElement: () => makeEl(),
  body: makeEl('body'),
  documentElement: makeEl('html'),
  addEventListener() {},
  querySelector: () => makeEl(),
  querySelectorAll: () => [],
};

const intervals = [];          // captured, never auto-fired
const sandboxWindow = {};
let liveText = null;           // what a live_payload fetch serves (armed later)

async function stubFetch(url) {
  url = String(url);
  if (url.includes('live_payload.js')) {
    if (liveText == null) return { ok: false, status: 404 };
    return { ok: true, status: 200, text: async () => liveText,
             json: async () => { throw new Error('not json'); } };
  }
  throw new Error('offline: ' + url);   // ticker etc. behave like no network
}

/* ---------------- load payload globals like <script src> ---------------- */
function evalPayloadFile(path) {
  // payload files are "window.ARENA_X = {...};"
  const src = fs.readFileSync(path, 'utf8');
  const fn = new Function('window', src);
  fn(sandboxWindow);
}

/* ---------------- run the page ---------------- */
async function main() {
  evalPayloadFile(indexPath.replace(/index\.html$/, 'prestart_payload.js'));
  evalPayloadFile(indexPath.replace(/index\.html$/, 'demo_payload.js'));
  if (payloadA && payloadA !== 'none') evalPayloadFile(payloadA);

  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .map((m) => m[1]);
  if (!scripts.length) throw new Error('no inline scripts found');

  const sandbox = {
    window: sandboxWindow, document,
    location: { search: '' },
    fetch: stubFetch,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval() {}, setTimeout: (fn) => { fn(); return 0; },
    Date, Math, JSON, Number, String, Array, Object, isFinite, parseFloat,
    parseInt, URLSearchParams, console,
    requestAnimationFrame: (fn) => fn(),
  };
  sandboxWindow.ARENA_STUB = true;
  // page code assigns window.setCoin etc. and reads bare globals
  const keys = Object.keys(sandbox);
  const body = scripts.join('\n;\n');
  const fn = new Function(...keys, `"use strict";\n${body}`);
  fn(...keys.map((k) => sandbox[k]));
  // allow queued microtasks (pollTicker/pollLive) to settle
  await new Promise((r) => setTimeout(r, 0));
  await Promise.resolve();

  const bootId = sandboxWindow.ARENA_LIVE
    ? sandboxWindow.ARENA_LIVE.publication_id : null;

  let polledId = null;
  if (payloadB) {
    // arm the "next published state" and fire the page's own poll interval —
    // this is exactly what an already-open browser does on its timer
    liveText = fs.readFileSync(payloadB, 'utf8');
    for (const { fn: cb } of intervals) await cb();
    await new Promise((r) => setTimeout(r, 0));
    polledId = sandboxWindow.ARENA_LIVE
      ? sandboxWindow.ARENA_LIVE.publication_id : null;
  }

  process.stdout.write(JSON.stringify({
    ok: true,
    boot_live_id: bootId,
    polled_live_id: polledId,
    mode: sandboxWindow.ARENA_LIVE ? sandboxWindow.ARENA_LIVE.mode
      : (sandboxWindow.ARENA_PRESTART || {}).mode || null,
  }) + '\n');
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({
    ok: false, error: String(e && e.stack || e),
  }) + '\n');
  process.exit(1);
});
