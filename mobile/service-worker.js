const CACHE = 'cortex-mobile-v1';
const ASSETS = ['/mobile', '/mobile/manifest.json'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/mobile')) {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
  }
});
