"use client";

import { useEffect, useId, useState } from "react";
import { Bell, BellOff } from "@/app/components/icons";
import { disablePush, enablePush, readPushState, resyncPush, sendTestPush, type PushState } from "@/lib/push";

type View =
  | { kind: "loading" }
  | { kind: "ready"; state: PushState; busy: boolean; note: string | null };

const STATE_TEXT: Record<PushState["kind"], string> = {
  unsupported: "Install the app (Add to Home screen) to get phone alerts.",
  blocked: "Notifications are blocked. Allow them in your phone's site settings.",
  off: "Get an alert when a strategy activates, closes or needs attention.",
  on: "On for this device.",
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Try again.";
}

/** Account-window section: turn trade alerts on or off for this device. */
export function NotificationSettings() {
  const labelId = useId();
  const [view, setView] = useState<View>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    void readPushState()
      .then(state => {
        if (!active) return;
        setView({ kind: "ready", state, busy: false, note: null });
        // Keeps the device bound to whoever is signed in now.
        if (state.kind === "on") void resyncPush().catch(() => undefined);
      })
      .catch(() => {
        if (active) setView({ kind: "ready", state: { kind: "unsupported" }, busy: false, note: null });
      });
    return () => {
      active = false;
    };
  }, []);

  if (view.kind === "loading") return null;
  const { state, busy, note } = view;

  async function run(action: () => Promise<PushState>, success: string | null) {
    setView({ kind: "ready", state, busy: true, note: null });
    try {
      const next = await action();
      setView({ kind: "ready", state: next, busy: false, note: next.kind === "on" ? success : null });
    } catch (error) {
      setView({ kind: "ready", state, busy: false, note: errorText(error) });
    }
  }

  async function test() {
    setView({ kind: "ready", state, busy: true, note: null });
    try {
      await sendTestPush();
      setView({ kind: "ready", state, busy: false, note: "Test sent. It should arrive in a few seconds." });
    } catch (error) {
      setView({ kind: "ready", state, busy: false, note: errorText(error) });
    }
  }

  return (
    <div className="account-section" role="group" aria-labelledby={labelId}>
      <span className="account-section-label" id={labelId}>Phone notifications</span>
      <small className="currency-rate-note">{STATE_TEXT[state.kind]}</small>
      {state.kind === "off" && (
        <button type="button" className="account-action" disabled={busy} onClick={() => void run(enablePush, "Notifications are on.")}>
          <Bell aria-hidden="true" />{busy ? "Turning on…" : "Turn on notifications"}
        </button>
      )}
      {state.kind === "on" && (
        <>
          <button type="button" className="account-action" disabled={busy} onClick={() => void test()}>
            <Bell aria-hidden="true" />Send a test notification
          </button>
          <button type="button" className="account-action" disabled={busy} onClick={() => void run(disablePush, null)}>
            <BellOff aria-hidden="true" />Turn off on this device
          </button>
        </>
      )}
      {note && <small className="currency-rate-note" role="status">{note}</small>}
    </div>
  );
}
