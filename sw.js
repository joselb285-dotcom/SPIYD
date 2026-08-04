const CACHE = 'spiyd-v4';
// '/mapa' no se precachea: requiere login y si no hay sesión redirige a /login,
// lo que cachearía una respuesta redirected:true bajo la key '/mapa'. Chrome
// rechaza servir eso para la navegación del start_url en la PWA instalada
// ("la página no funciona / se trasladó permanentemente").
const PRECACHE = ['/manifest.json', '/pwa-icon.svg', '/i18n.js', '/pwa-install.js'];
// Archivos que cambian seguido — siempre priorizar la red, caché solo como fallback offline.
const NETWORK_FIRST = ['/i18n.js', '/theme.js', '/pwa-install.js'];

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
  '/nasa-focos', '/smn-alertas', '/inpe-focos',
  '/admin', '/superadmin', '/ai-', '/fwi-grid', '/wind-data',
  '/ai-foco-analysis', '/ai-zona-analysis', '/login', '/logout',
  '/precipitacion', '/vegetation', '/water-sources',
  '/weather-grid', '/weather-grid-zona', '/foco-clima-4d',
  '/sismos', '/volcanes', '/telegram-alerta', '/verify-email',
  // Hidrología: cambia cada 15 min - 1 h, el backend ya cachea del lado servidor.
  '/ina-estaciones', '/ina-serie', '/geoglows-forecast', '/ana-estaciones', '/ana-serie'
];

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const path = new URL(e.request.url).pathname;
  if (API_PREFIXES.some(p => path.startsWith(p))) return;

  if (NETWORK_FIRST.includes(path)) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.status === 200 && res.type === 'basic' && !res.redirected) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (res && res.status === 200 && res.type === 'basic' && !res.redirected) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      });
    })
  );
});
