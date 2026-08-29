"use client";

import { useCallback, useEffect, useId, useState, useSyncExternalStore } from "react";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { AddToHomeScreen, ShareSheet, Smartphone, X } from "@/app/components/icons";
import { useDisclosure } from "@/app/components/ui";
import {
  captureInstallOffers, installOfferHeld, installOfferSnoozed, needsManualInstallSteps,
  noInstallOffer, requestInstall, runningInstalled, snoozeInstallOffer, subscribeToInstallOffer,
  syncServiceWorker
} from "@/lib/pwa";

/**
 * Phones and small tablets only, which is the whole point of installing this
 * app: a laptop already has it in a browser window that behaves correctly, and
 * Chrome's own address-bar install button covers the rare desktop case without
 * a banner sitting over the workspace. `pointer: coarse` is the part that keeps
 * a narrow desktop window out: a touchscreen laptop still reports a fine
 * primary pointer because of its trackpad. The width is 1024px rather than a
 * phone's portrait width because a large phone in landscape is 930px across.
 */
const PHONE_QUERY = "(max-width: 1024px) and (pointer: coarse)";

/** Routes mid-flow, where an install offer would be an interruption. */
const QUIET_PATHS = ["/reset-password", "/auth"];

/**
 * How long Safari users get before the offer appears. Chrome waits for its own
 * engagement signal before firing `beforeinstallprompt`, so it needs no delay;
 * Safari has no such event and the app has to pick the moment itself.
 */
const MANUAL_OFFER_DELAY_MS = 15_000;

/**
 * App-wide progressive web app behaviour: registers the service worker, starts
 * holding install offers, and renders the offer when there is one.
 *
 * Mounted from the root layout rather than from a page, because
 * `beforeinstallprompt` fires once per page load at a moment Chrome chooses. A
 * listener attached later than that moment never hears it, and the offer is
 * lost for the session.
 */
export default function PwaRuntime() {
  const pathname = usePathname();

  useEffect(() => {
    const release = captureInstallOffers();
    // Registration competes with the page's own requests, so it waits for the
    // load event. The worker is only needed from the next navigation onwards.
    const register = () => syncServiceWorker();
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });

    return () => {
      release();
      window.removeEventListener("load", register);
    };
  }, []);

  if (QUIET_PATHS.some(path => pathname.startsWith(path))) return null;
  return <InstallOffer />;
}

/** How this browser can install, once the client has been able to look. */
type InstallRoute = "unknown" | "browser-prompt" | "manual-steps";

function InstallOffer() {
  const offerHeld = useSyncExternalStore(subscribeToInstallOffer, installOfferHeld, noInstallOffer);
  const onPhone = useMediaMatch(PHONE_QUERY);
  const [route, setRoute] = useState<InstallRoute>("unknown");
  const [turnedDown, setTurnedDown] = useState(false);
  const titleId = useId();

  /**
   * Everything this decides — whether the app is already installed, whether the
   * offer is snoozed, which browser this is — reads a browser API that either
   * does not exist on the server or answers differently there. Deciding in an
   * effect keeps the first client render identical to the server's.
   */
  useEffect(() => {
    if (runningInstalled() || installOfferSnoozed()) return;
    if (!needsManualInstallSteps()) {
      setRoute("browser-prompt");
      return;
    }
    const timer = window.setTimeout(() => setRoute("manual-steps"), MANUAL_OFFER_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  const offering = route === "manual-steps" || (route === "browser-prompt" && offerHeld);
  const open = onPhone && offering && !turnedDown;
  // Held in the tree for the length of its close transition, so dismissing it
  // slides out instead of vanishing between frames.
  const sheet = useDisclosure(open, "--panel-close-dur");

  const dismiss = useCallback(() => {
    snoozeInstallOffer();
    setTurnedDown(true);
  }, []);

  /**
   * The sheet closes as soon as the browser takes over, because the held offer
   * is spent the moment it is handed across and a banner still claiming an
   * install is available would be lying. The outcome only decides whether the
   * offer is snoozed.
   */
  const install = useCallback(() => {
    void requestInstall().then(outcome => {
      // A dialog the user closed is a "no" worth remembering. An unavailable
      // prompt is not: the browser may well offer it again next load.
      if (outcome === "dismissed") snoozeInstallOffer();
      setTurnedDown(true);
    });
  }, []);

  if (!sheet.mounted) return null;

  const heading = route === "manual-steps" ? "Add to your home screen" : "Install Trade Cognition";

  return (
    <aside
      className="install-sheet t-panel-slide"
      data-open={sheet.state === "open"}
      aria-labelledby={titleId}
    >
      <span className="install-sheet-mark" aria-hidden="true">
        <Image src="/polycognition-mark.png" alt="" width={36} height={36} />
      </span>

      <div className="install-sheet-copy">
        <strong id={titleId}>{heading}</strong>
        <p>
          {route === "manual-steps"
            ? "It opens full screen, without Safari's toolbars taking a third of the chart."
            : "It opens full screen from your home screen, with no browser bars over the chart."}
        </p>
      </div>

      <button
        type="button"
        className="install-sheet-dismiss"
        onClick={dismiss}
        aria-label="Dismiss the install offer"
      >
        <X aria-hidden="true" />
      </button>

      {route === "manual-steps" ? (
        <ol className="install-sheet-steps">
          <li>
            <ShareSheet aria-hidden="true" />
            <span>Tap <strong>Share</strong> in the Safari toolbar</span>
          </li>
          <li>
            <AddToHomeScreen aria-hidden="true" />
            <span>Choose <strong>Add to Home Screen</strong></span>
          </li>
        </ol>
      ) : (
        <div className="install-sheet-actions">
          <button type="button" className="button primary" onClick={install}>
            <Smartphone aria-hidden="true" />Install app
          </button>
          <button type="button" className="button ghost" onClick={dismiss}>Not now</button>
        </div>
      )}
    </aside>
  );
}

/** Subscribes to a media query, so rotating a phone re-decides rather than sticking. */
function useMediaMatch(query: string) {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const media = window.matchMedia(query);
    setMatches(media.matches);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
