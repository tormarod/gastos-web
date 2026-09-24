// Gastos service worker (served at /sw.js so it covers the whole app).
//
// It does one thing: when a page takes too long (the server on Render is
// waking up) or can't be fetched (no connection), it shows the waiting screen
// instead of a blank one. That screen reloads the page once the server answers.
// Nothing with your data is ever stored on the phone: only the waiting screen.

const CACHE = 'gastos-{{ asset_version }}';
const WAIT_PAGE = '/espera';
const SLOW_MS = {{ slow_ms }};

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.add(new Request(WAIT_PAGE, { cache: 'reload' })))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('gastos-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  // Only opening a page; forms, styles and everything else go to the network as always.
  if (request.method !== 'GET' || request.mode !== 'navigate') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname === WAIT_PAGE) return;

  event.respondWith(new Promise((resolve) => {
    let answered = false;
    const answer = (response) => {
      if (answered) return;
      answered = true;
      clearTimeout(timer);
      resolve(response);
    };
    const waiting = () => caches.match(WAIT_PAGE).then((page) => page || Response.error());
    const timer = setTimeout(() => waiting().then(answer), SLOW_MS);
    fetch(request).then(answer, () => waiting().then(answer));
  }));
});
