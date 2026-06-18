const CACHE = 'spiyd-v1';
const PRECACHE = ['/mapa', '/manifest.json', '/pwa-icon.svg', '/i18n.js'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// API paths that must never be cached
const API_PREFIXES = [
  '/nasa-focos', '/smn-alertas', '/inpe-focos', '/goes-focos',
  '/admin', '/ai-', '/zona-clima', '/fwi-grid', '/wind-data',
  '/ai-foco-analysis', '/ai-zona-analysis', '/login', '/logout'
];

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const path = new URL(e.request.url).pathname;
  if (API_PREFIXES.some(p => path.startsWith(p))) return;

  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (res && res.status === 200 && res.type === 'basic') {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      });
    })
  );
});
