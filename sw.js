/* ValleyMind AI service worker.
 *
 * Strategy:
 *   - PRECACHE the app shell so the SPA boots offline on repeat visits.
 *   - Navigations (page loads): network-first, falling back to the cached
 *     shell so the app still opens without a connection.
 *   - Same-origin GETs to non-app resources (static assets): stale-while-
 *     revalidate (instant cache hit + background refresh).
 *   - User-data endpoints (/api/, /auth/, /chat, /tts, /static/media) are
 *     deliberately NOT cached to avoid serving stale personal data.
 *
 * Bump VERSION to invalidate all cached entries after a deploy.
 */
const VERSION = 'valleymind-v3';
const PRECACHE = [
  '/',
  '/manifest.json',
  '/static/valleymind-logo.png',
  '/static/icons/icon-192.png'
];

// Path prefixes whose responses must never be stored in the cache.
const NEVER_CACHE = ['/api/', '/auth/', '/chat', '/suggestions', '/tts/', '/static/media/'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(VERSION).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== VERSION).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // never touch cross-origin
  if (NEVER_CACHE.some(prefix => url.pathname.startsWith(prefix))) return; // default network

  // Navigation requests: try the network first, fall back to the cached shell.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          const copy = response.clone();
          caches.open(VERSION).then(cache => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then(cached => cached || caches.match('/')))
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  event.respondWith(
    caches.match(request).then(cached => {
      const refresh = fetch(request).then(response => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then(cache => cache.put(request, copy));
        }
        return response;
      }).catch(() => cached);
      return cached || refresh;
    })
  );
});
