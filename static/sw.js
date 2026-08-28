const CACHE_NAME = 'rpinode-v7';
const ASSETS = [
  '/rpinode/',
  '/rpinode/manifest.json',
  '/rpinode/static/style.css',
  '/rpinode/static/app.js',
  '/rpinode/static/DELTA-Thermic-v3_reverse.png'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // NE JAMAIS mettre en cache :
  // 1. Les appels API
  // 2. Les pages de scan (doivent être fraîches)
  // 3. Les flux SSE
  if (url.includes('/api/') || url.includes('/scan/') || url.includes('/monitor/')) {
    return;
  }
  
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request)
        .then((networkResponse) => {
          // On ne met en cache que les assets statiques et la racine
          if (url.includes('/static/') || url.endsWith('/rpinode/')) {
            return caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, networkResponse.clone());
              return networkResponse;
            });
          }
          return networkResponse;
        })
        .catch(() => {
          return new Response(null, { status: 503, statusText: 'Hors ligne' });
        });
    })
  );
});
