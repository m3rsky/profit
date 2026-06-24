const CACHE_VERSION = 'psh-qc-v9';
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const ALL_CACHES    = [STATIC_CACHE];

const PRECACHE_STATIC = [
  '/static/css/main.css',
  '/static/js/app.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/manifest.json',
  '/static/offline.html',
];

// ── Install ──────────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(PRECACHE_STATIC))
      .then(() => self.skipWaiting())
  );
});

// ── Activate ─────────────────────────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => !ALL_CACHES.includes(k)).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Tylko same-origin
  if (url.origin !== self.location.origin) return;

  // Strony HTML (nawigacja) – zawsze sieć, nigdy cache
  // HTML zawiera dane użytkownika – cachowanie powoduje wyświetlanie
  // danych jednego użytkownika innym użytkownikom.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/static/offline.html'))
    );
    return;
  }

  // API, uploads, auth – zawsze sieć
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/uploads/') ||
      url.pathname === '/logout') {
    event.respondWith(fetch(request));
    return;
  }

  // Statyczne zasoby – cache-first z aktualizacją w tle
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Reszta – sieć
  event.respondWith(fetch(request));
});

// ── Cache-first (tylko dla statycznych zasobów) ───────────────────────────────
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    fetch(request).then(response => {
      if (response.ok) {
        caches.open(STATIC_CACHE).then(c => c.put(request, response));
      }
    }).catch(() => {});
    return cached;
  }
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    cache.put(request, response.clone());
  }
  return response;
}
