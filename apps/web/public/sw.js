// LLMBook service worker. Its only job: when the server (someone's laptop) is off or
// unreachable, show a friendly offline page instead of the browser's error page.
// Nothing else is cached; API calls and documents always go to the network.

const CACHE = "llmbook-offline-v1";
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add(OFFLINE_URL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request)
      // 502-504: the tunnel is up but LLMBook behind it isn't (Docker stopped, restarting).
      .then((response) => (response.status >= 502 ? offline() : response))
      .catch(offline),
  );
});

function offline() {
  return caches.match(OFFLINE_URL);
}
