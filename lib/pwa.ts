/**
 * Install and service-worker plumbing.
 *
 * Kept out of the component layer because none of it is rendering: it is
 * browser-API handling with two awkward edges that are easier to get right in
 * one typed place.
 *
 * The first edge is `beforeinstallprompt`. Chrome fires it once per page load,
 * at a moment of its own choosing after the visitor has interacted with the
 * page, and the event is single-use. A component that mounts later than the
 * event fires would simply never see it, so the listener is attached at app
 * boot and the event is held in a module-level store that any component can
 * subscribe to.
 *
 * The second edge is that none of these APIs exist on the server, and two of
 * them (`localStorage`, `matchMedia`) would produce a different answer during
 * hydration than during the server render. Every function here is therefore
 * safe to call from an effect and none of them should be called during render.
 */

/** Snooze length after the offer is turned down, so it asks again eventually. */
const SNOOZE_MS = 14 * 24 * 60 * 60 * 1_000;
const SNOOZE_KEY = "trade-cognition-install-snoozed-at";
const SERVICE_WORKER_URL = "/sw.js";

/**
 * The worker is registered in production only. In development the app is served
 * from a build that changes on every keystroke, and a worker sitting in front of
 * those navigations is a source of confusion with nothing to gain.
 */
const SERVICE_WORKER_ENABLED = process.env.NODE_ENV === "production";

export type InstallOutcome = "accepted" | "dismissed" | "unavailable";

/**
 * Not in the DOM typings: `beforeinstallprompt` is Chromium-only and still
 * outside the standard. Declared as the two members that are actually used.
 */
type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  readonly userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

function isBeforeInstallPromptEvent(event: Event): event is BeforeInstallPromptEvent {
  if (!("prompt" in event) || !("userChoice" in event)) return false;
  return typeof event.prompt === "function" && event.userChoice instanceof Promise;
}

/* ------------------------------------------------------------------ *
 * The held prompt
 * ------------------------------------------------------------------ */

let heldPrompt: BeforeInstallPromptEvent | null = null;
const subscribers = new Set<() => void>();

function publish() {
  for (const notify of subscribers) notify();
}

export function subscribeToInstallOffer(onChange: () => void): () => void {
  subscribers.add(onChange);
  return () => {
    subscribers.delete(onChange);
  };
}

/**
 * A boolean rather than the event itself, because `useSyncExternalStore`
 * re-renders whenever the snapshot is not reference-equal to the last one.
 */
export function installOfferHeld(): boolean {
  return heldPrompt !== null;
}

/** The server has no prompt to offer, and neither does the first client render. */
export function noInstallOffer(): boolean {
  return false;
}

/**
 * Starts holding install offers. Returns the teardown, so a component can own
 * the listener's lifetime with an effect.
 */
export function captureInstallOffers(): () => void {
  const onOffer = (event: Event) => {
    if (!isBeforeInstallPromptEvent(event)) return;
    // Chrome shows a mini-infobar of its own unless the event is cancelled. The
    // app asks in its own surface instead, at a size a thumb can actually hit.
    event.preventDefault();
    heldPrompt = event;
    publish();
  };

  const onInstalled = () => {
    heldPrompt = null;
    publish();
  };

  window.addEventListener("beforeinstallprompt", onOffer);
  window.addEventListener("appinstalled", onInstalled);

  return () => {
    window.removeEventListener("beforeinstallprompt", onOffer);
    window.removeEventListener("appinstalled", onInstalled);
  };
}

/**
 * Hands the held event to the browser, which shows the real install dialog.
 *
 * The event is discarded first because it cannot be prompted twice: a second
 * call throws, and holding a spent event would leave the banner claiming an
 * install is available when it no longer is.
 */
export async function requestInstall(): Promise<InstallOutcome> {
  const offer = heldPrompt;
  if (!offer) return "unavailable";
  heldPrompt = null;
  publish();

  try {
    await offer.prompt();
    const { outcome } = await offer.userChoice;
    return outcome;
  } catch {
    return "unavailable";
  }
}

/* ------------------------------------------------------------------ *
 * Where the app is running
 * ------------------------------------------------------------------ */

const DISPLAY_QUERIES = ["standalone", "fullscreen", "minimal-ui"] as const;

function hasIosStandaloneFlag(candidate: Navigator): candidate is Navigator & { standalone: boolean } {
  return "standalone" in candidate && typeof candidate.standalone === "boolean";
}

/** True once the app has been installed and is being run from the home screen. */
export function runningInstalled(): boolean {
  if (typeof window === "undefined") return false;
  for (const mode of DISPLAY_QUERIES) {
    if (window.matchMedia(`(display-mode: ${mode})`).matches) return true;
  }
  // iOS predates `display-mode` and reports standalone on the navigator.
  const agent = window.navigator;
  return hasIosStandaloneFlag(agent) && agent.standalone;
}

/**
 * True on the iOS browsers that can install an app but never fire
 * `beforeinstallprompt`, which is Safari and only Safari. Chrome, Firefox and
 * Edge on iOS have no add-to-home-screen item at all, so pointing their users
 * at the share sheet would send them down a dead end.
 */
export function needsManualInstallSteps(): boolean {
  if (typeof window === "undefined") return false;
  const { userAgent, maxTouchPoints } = window.navigator;
  const iphone = /iP(?:hone|od|ad)/.test(userAgent);
  // iPadOS 13 and later report a desktop Safari user agent.
  const ipad = /Macintosh/.test(userAgent) && maxTouchPoints > 1;
  if (!iphone && !ipad) return false;
  return !/CriOS|FxiOS|EdgiOS|OPiOS|Chrome/.test(userAgent);
}

/* ------------------------------------------------------------------ *
 * Snoozing
 * ------------------------------------------------------------------ */

export function installOfferSnoozed(now = Date.now()): boolean {
  try {
    const stored = window.localStorage.getItem(SNOOZE_KEY);
    if (stored === null) return false;
    const snoozedAt = Number.parseInt(stored, 10);
    if (!Number.isFinite(snoozedAt)) return false;
    const age = now - snoozedAt;
    // A timestamp in the future means a moved clock, not a real snooze.
    return age >= 0 && age < SNOOZE_MS;
  } catch {
    return false;
  }
}

export function snoozeInstallOffer(now = Date.now()): void {
  try {
    window.localStorage.setItem(SNOOZE_KEY, String(now));
  } catch {
    // Private browsing and a full quota both throw here. Losing the snooze only
    // means the offer returns on the next visit, which is not worth surfacing.
  }
}

/* ------------------------------------------------------------------ *
 * Service worker
 * ------------------------------------------------------------------ */

/**
 * Registers the worker, or clears any worker left behind by an earlier build
 * when running outside production.
 */
export function syncServiceWorker(): void {
  if (typeof window === "undefined" || !("serviceWorker" in window.navigator)) return;
  const container = window.navigator.serviceWorker;

  if (!SERVICE_WORKER_ENABLED) {
    void container.getRegistrations()
      .then(registrations => Promise.all(registrations.map(registration => registration.unregister())))
      .catch(() => {
        // Nothing to clean up, or the browser refused. Either way the app runs.
      });
    return;
  }

  void container.register(SERVICE_WORKER_URL, { scope: "/" }).catch(() => {
    // A worker that will not register costs the install offer and the offline
    // page. The app itself is unaffected, so this is not an error to report.
  });
}
