/* Wolf Capital — minimal shell cache for PWA installability */
const CACHE = 'wolf-capital-v2';
const SHELL = [
  '/app',
  '/index.html',
  '/wolf_logo.png',
  '/support.js',
  '/config.js',
  '/manifest.webmanifest',
];

// The app shell + runtime scripts change often during active development —
// always prefer a fresh network fetch for these so UI/auth fixes show up
// immediately, and only fall back to the cached copy when offline.
const NETWORK_FIRST_PATHS = new Set(['/app', '/', '/index.html', '/support.js', '/config.js']);

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/health')) return;

  if (request.mode === 'navigate' || NETWORK_FIRST_PATHS.has(url.pathname)) {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => undefined);
          return res;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});
