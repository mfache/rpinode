const CACHE_NAME = 'rpinode-v9';
const ASSETS = [
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
            console.log('Suppression ancien cache SW:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // NE JAMAIS mettre en cache :
  // 1. Les appels API
  // 2. Les pages de scan (doivent être fraîches)
  // 3. Les flux SSE
  // 4. Les requêtes non-GET
  if (event.request.method !== 'GET' || url.includes('/api/') || url.includes('/scan/') || url.includes('/monitor/')) {
    return;
  }

  // Pour les pages HTML / Navigation (ex: /rpinode/) : NETWORK FIRST
  // On va chercher la dernière version sur le serveur, et on ne se replie sur le cache que si on est hors-ligne.
  if (event.request.mode === 'navigate' || url.endsWith('/rpinode') || url.endsWith('/rpinode/')) {
    event.respondWith(
      fetch(event.request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(() => {
          return caches.match(event.request).then((cached) => {
            return cached || new Response("Hors ligne", { status: 503, statusText: 'Hors ligne' });
          });
        })
    );
    return;
  }

  // Pour les assets statiques (CSS, JS, images) : CACHE FIRST avec mise à jour en tâche de fond
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        // En tâche de fond, rafraîchir le cache
        fetch(event.request).then((freshResponse) => {
          if (freshResponse && freshResponse.status === 200) {
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, freshResponse));
          }
        }).catch(() => {});
        return cachedResponse;
      }
      return fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && url.includes('/static/')) {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseClone));
        }
        return networkResponse;
      });
    })
  );
});
