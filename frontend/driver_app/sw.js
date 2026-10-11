// Service worker: cache the app shell so the driver app opens offline.
// API calls are never cached - the app.js queue handles offline deliveries.
// Bump the version string whenever index.html / app.js change, so the
// old cached copy is thrown away instead of being served forever.
const CACHE = "driver-shell-v2";
const SHELL = ["./", "./index.html", "./app.js", "./manifest.webmanifest",
               "./icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE)
                       .map((k) => caches.delete(k)))));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/driver/") || url.pathname.startsWith("/ws/")) {
    return; // network only; offline handling lives in app.js
  }
  // network-first: always try a fresh copy, fall back to cache offline
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok && event.request.method === "GET") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(event.request, { ignoreSearch: true })));
});
