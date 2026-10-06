const CACHE = 'dashboard-v3';
const ASSETS = [
  '/dashboard/',
  '/dashboard/index.html',
  '/dashboard/css/app.css',
  '/dashboard/js/app.js?v=3',
  '/dashboard/manifest.webmanifest',
  '/dashboard/roadmap.json',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return;
  // HTML/JS/CSS 用 network first（确保最新版），其他静态资源用 cache first
  const isHtml = url.pathname.endsWith('.html') || url.pathname === '/dashboard/' || url.pathname === '/dashboard';
  const isJsCss = url.pathname.match(/\.(js|css|json)$/);
  if (isHtml || isJsCss) {
    e.respondWith(
      fetch(e.request).then(resp => {
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone)).catch(() => {});
        return resp;
      }).catch(() => caches.match(e.request))
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(resp => {
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone)).catch(() => {});
        return resp;
      }).catch(() => cached))
    );
  }
});
