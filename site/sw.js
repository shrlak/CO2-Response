// CO2-Response service worker: keeps the site working without internet.
//
// The page, the Python package and the Python runtime (Pyodide, from the jsDelivr CDN)
// are stored in the browser the first time the site is opened online. After that the
// monitor starts with no network at all, e.g. on a lab or hospital network that blocks
// the internet. Nothing recorded is stored here: sessions live in IndexedDB.
//
// - CDN files have their version in the URL and never change: served from the cache.
// - The site's own files: fetched from the network when there is one, so a new deploy
//   arrives at once; the stored copy is used only when the network fails.

const CACHE = 'co2-response-v1';
const CDN = 'cdn.jsdelivr.net';

const mine = (url) => url.origin === self.location.origin;
const keep = (res) => res && (res.ok || res.type === 'opaque');

async function store(req) {
  const cache = await caches.open(CACHE);
  const res = await fetch(req);
  if (keep(res)) await cache.put(req, res.clone());
  return res;
}

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (ev) => {
  ev.waitUntil(
    (async () => {
      for (const name of await caches.keys()) if (name !== CACHE) await caches.delete(name);
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (ev) => {
  const req = ev.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.hostname === CDN) {
    ev.respondWith(caches.match(req).then((hit) => hit || store(req)));
  } else if (mine(url)) {
    // ignoreSearch: "?sim" and the like are the same page
    ev.respondWith(store(req).catch(async () => (await caches.match(req, { ignoreSearch: true })) || Response.error()));
  }
});

// The first visit loads everything before this worker is in control, so the page
// sends the list of what it loaded and it is stored here (from the browser's own
// HTTP cache, without downloading again where possible).
self.addEventListener('message', (ev) => {
  const urls = (ev.data && ev.data.store) || [];
  ev.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      let stored = 0;
      for (const u of urls) {
        const url = new URL(u);
        if (url.hostname !== CDN && !mine(url)) continue;
        try {
          if (!(await cache.match(u))) {
            const res = await fetch(u, { mode: 'cors' });
            if (keep(res)) await cache.put(u, res);
          }
          stored++;
        } catch (e) {
          /* offline, or the file is gone: try again on the next visit */
        }
      }
      if (ev.source) ev.source.postMessage({ stored, of: urls.length });
    })(),
  );
});
