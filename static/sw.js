const CACHE_NAME = 'rpinode-v3';
const ASSETS = [
  '/rpinode/',
  '/rpinode/manifest.json',
  '/rpinode/static/style.css',
  '/rpinode/static/app.js',
  '/rpinode/static/DELTA-Thermic-v3_reverse.png'
];

self.addEventListener('install', (event) => {
  self.skipWaiting(); // Force le nouveau SW à s'activer immédiatement
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

self.addEventListener('activate', (event) => {
  // Supprime les anciens caches
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
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      // Si une ressource est en cache, la retourner
      if (cachedResponse) {
        return cachedResponse;
      }
      // Sinon, tenter de la récupérer du réseau
      return fetch(event.request)
        .then((networkResponse) => {
          // Si la requête réseau réussit, mettre en cache et retourner la réponse
          return caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        })
        .catch(() => {
          // Si le réseau échoue, et que la ressource n'était pas en cache,
          // on peut potentiellement servir une page hors ligne ou une image de fallback.
          // Pour l'instant, nous laissons l'erreur se propager si rien n'est en cache.
          // Pour les assets spécifiés, nous devrions les avoir dans le cache.
          // Ici, on pourrait ajouter une logique pour des fallbacks spécifiques.
          console.log('Fetch failed, and no cache match for', event.request.url);
          // Retourne une erreur ou un fallback générique si nécessaire
          return new Response(null, { status: 503, statusText: 'Service Unavailable (Offline)' });
        });
    })
  );
});
