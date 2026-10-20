// Bumping the name makes activate delete every older cache (v6: 10/19/26).
const CACHE_NAME = 'baseline-v6';
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/offline'
];

// Install — pre-cache static assets + offline page
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate — clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — network-only for navigation, network-first for CSS/JS, cache-first for other static assets
self.addEventListener('fetch', event => {
  const { request } = event;

  // Navigation requests (HTML pages): network only, offline page as the fallback.
  // Pages are never cached — they carry health data and must not stay on the
  // device after logout. The server also marks them no-store.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/offline').then(r => r || new Response('Offline')))
    );
    return;
  }

  // CSS and JS: network-first so style/script updates reach users without
  // a service-worker version bump. Fall back to cache when offline.
  if (request.url.match(/\/static\/.*\.(css|js)$/)) {
    event.respondWith(
      fetch(request)
        .then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Other static assets (images, icons, manifest): cache-first.
  if (request.url.includes('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
          return response;
        });
      })
    );
    return;
  }

  // Everything else: network only
  event.respondWith(fetch(request));
});
