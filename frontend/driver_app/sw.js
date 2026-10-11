// Service worker: cache the app shell so the driver app opens offline.
// API calls are never cached - the app.js queue handles offline deliveries.
const CACHE = "driver-shell-v1";
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
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true })
      .then((hit) => hit || fetch(event.request)));
});
