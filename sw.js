const V = 'trance-shell-v25';
const SHELL =
    ['./', 'index.html', 'manifest.webmanifest', 'data/icon.svg', 'data/icon-192.png',
     'data/icon-512.png', 'data/fnf arrows.png'];
self.addEventListener(
    'install',
    e => {e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()))});
self.addEventListener(
    'activate',
    e => {e.waitUntil(
        caches.keys()
            .then(
                k => Promise.all(
                    k.filter(x => x !== V && x !== 'trance-fonts').map(x => caches.delete(x))))
            .then(() => self.clients.claim()))});
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin === location.origin) {
    const p = u.pathname,
          shell = p === '/' ||
              /\/ (index\.html|manifest\.webmanifest|icon[^/]*)$/.test(p) ||
              /^\/data\/(?:fnf(?:%20| )arrows\.png|character\.png)$/i.test(p);
    if (!shell) return;
    e.respondWith(fetch(r)
                      .then(res => {
                        const cp = res.clone();
                        caches.open(V).then(c => c.put(r, cp));
                        return res
                      })
                      .catch(() => caches.match(r).then(m => m || caches.match('index.html'))));
    return
  }
  if (/(^|\.)googleapis\.com$|(^|\.)gstatic\.com$/.test(u.hostname)) {
    e.respondWith(
        caches.open('trance-fonts').then(c => c.match(r).then(m => m || fetch(r).then(res => {
          c.put(r, res.clone());
          return res
        }))))
  }
});
