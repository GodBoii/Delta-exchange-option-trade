/*
 * Trade Cognition service worker.
 *
 * This worker caches nothing. That is a decision, not an omission. Every
 * surface in the app reads live exchange state — balances, positions, option
 * chains, scheduled runs — and a cached shell would either show yesterday's
 * figures or run a stale bundle against a redeployed backend. Neither is an
 * acceptable failure mode on a trading screen.
 *
 * It exists for two narrower reasons:
 *
 *   1. Chrome only offers to install an app whose service worker handles
 *      `fetch`. Without this file the browser never fires
 *      `beforeinstallprompt`, so there is no way to put the app on a phone's
 *      home screen.
 *   2. A page opened with no connection should say so in the app's own voice
 *      rather than showing the browser's error screen, which in a standalone
 *      window has no address bar to retry from.
 *
 * Only a failed top-level navigation is answered here. Everything else — every
 * script, style, image, API call and websocket — is left alone, so installing
 * this worker changes nothing about how the app behaves online.
 */

self.addEventListener("install", () => {
  // Nothing to precache, so the new worker replaces the old one immediately
  // instead of waiting for every tab to close.
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", event => {
  const { request } = event;
  // `navigate` covers exactly the case worth handling: the document request the
  // browser makes when someone opens or reloads a page.
  if (request.method !== "GET" || request.mode !== "navigate") return;
  event.respondWith(networkFirst(request));
});

/**
 * The network answer always wins, including redirects: a navigation request
 * carries `redirect: "manual"`, so the OAuth callback's 302 comes back as an
 * opaque redirect and the browser follows it as it normally would.
 */
async function networkFirst(request) {
  try {
    return await fetch(request);
  } catch {
    return offlineResponse(request);
  }
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, character => HTML_ESCAPES[character]);
}

/**
 * A self-contained page: no stylesheet, no font, no script, nothing that could
 * itself be missing from the network. It answers with 200 because the installed
 * app's start URL has to resolve for the browser to treat the app as working
 * offline, and a 5xx there reads as a broken install.
 */
function offlineResponse(request) {
  const retryPath = escapeHtml(new URL(request.url).pathname || "/");
  const body = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Offline · Trade Cognition</title>
<style>
  :root { color-scheme: dark light; --bg: #080809; --fg: #f5f4f2; --muted: #9a9aa0; --line: #26262c; }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f4f2ee; --fg: #14140f; --muted: #5f5c53; --line: #e2ded4; }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 12px;
    padding: 32px 24px;
    background: var(--bg);
    color: var(--fg);
    text-align: center;
    font: 400 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  h1 { margin: 0; font-size: 19px; letter-spacing: -0.01em; }
  p { margin: 0; max-width: 34ch; color: var(--muted); }
  a {
    margin-top: 12px;
    padding: 12px 22px;
    min-height: 44px;
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--line);
    border-radius: 10px;
    color: var(--fg);
    font-weight: 600;
    text-decoration: none;
  }
</style>
</head>
<body>
  <h1>You are offline</h1>
  <p>Trade Cognition needs a connection to read balances, positions and scheduled runs. Nothing has been lost.</p>
  <a href="${retryPath}">Try again</a>
</body>
</html>`;

  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store"
    }
  });
}
