"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, KeyRound, LoaderCircle, LockKeyhole, ShieldCheck, Zap } from "lucide-react";
import { MetalFx } from "metal-fx";
import { requestJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import type { Account, AppUser } from "@/lib/app-types";
import {
  Brand, Field, InlineMessage, readMs, Shimmer, SuccessCheck, useShake
} from "@/app/components/ui";

/**
 * One-time Delta Exchange connection.
 *
 * This is the only screen where a live trading credential is entered, so it
 * states plainly what happens to the secret and what does not happen on submit
 * (no order is placed). Trading access is verified server-side before the
 * connection is stored.
 */
export default function ConnectView({ user, onConnected, onSignOut, embedded = false }: {
  user: AppUser;
  onConnected: (account: Account) => void;
  onSignOut: () => void;
  embedded?: boolean;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [verified, setVerified] = useState(false);
  const [shown, setShown] = useState(false);
  const { target: card, shake } = useShake<HTMLElement>();

  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await requestJson<{ account: Account }>("/api/session/connect", {
        method: "POST",
        body: JSON.stringify({ apiKey, apiSecret })
      });
      setApiKey("");
      setApiSecret("");
      /**
       * The check is held for exactly its own animation before the workspace
       * takes over. This is the one place a deliberate pause is worth it: a live
       * trading credential was just accepted by the exchange, and confirming
       * that explicitly is better than the screen vanishing mid-verification.
       */
      setVerified(true);
      window.setTimeout(() => onConnected(result.account), readMs("--check-opacity-dur", 500) + 250);
    } catch (nextError) {
      setError(errorMessage(nextError));
      shake();
      setBusy(false);
    }
  }

  return (
    <div className={`entry-shell${embedded ? " entry-shell--embedded" : ""}`}>
      {!embedded && (
        <header className="entry-bar">
          <Brand />
          <div className="entry-bar-actions">
            <span className="entry-user">{user.displayName || user.email}</span>
            <button type="button" className="button ghost" onClick={onSignOut}>Sign out</button>
          </div>
        </header>
      )}

      <div className="entry-grid">
        <section className={`entry-copy t-stagger${shown ? " is-shown" : ""}`}>
          <p className="eyebrow t-stagger-line t-stagger-line--1"><span aria-hidden="true" />One-time setup</p>
          <h1 className="t-stagger-line t-stagger-line--2">Connect your Delta Exchange account.</h1>
          <p className="entry-lede t-stagger-line t-stagger-line--3">
            Use your Delta API credentials to schedule strategies and manage positions from this dashboard.
          </p>

          <dl className="entry-facts t-stagger-line t-stagger-line--4">
            <div>
              <dt><ShieldCheck aria-hidden="true" />Encrypted credential storage</dt>
              <dd>Your API secret is encrypted and is never displayed after you connect.</dd>
            </div>
            <div>
              <dt><LockKeyhole aria-hidden="true" />Verified before connecting</dt>
              <dd>Trading permission is checked against Delta before the connection is accepted.</dd>
            </div>
            <div>
              <dt><KeyRound aria-hidden="true" />Revocable at any time</dt>
              <dd>Disconnecting deletes the stored secret and leaves your saved strategies intact.</dd>
            </div>
          </dl>
        </section>

        <div className="entry-card-shell">
          <section className={`entry-card t-input${error ? " is-error" : ""}`} ref={card}>
            {verified ? (
              <div className="connect-verified" role="status">
                <SuccessCheck shown label="Trading access verified" size={56} />
                <strong>Trading access verified</strong>
                <small>Opening your dashboard</small>
              </div>
            ) : (
              <>
                <header className="entry-card-head">
                  <span className="panel-icon" aria-hidden="true"><KeyRound /></span>
                  <div>
                    <h2>Delta Exchange India</h2>
                    <p>Live Delta account.</p>
                  </div>
                </header>

                <InlineMessage tone="warning">
                  This connects to your live Delta account. No order is placed until a strategy reaches
                  its scheduled entry time.
                </InlineMessage>

                <form className="stack" onSubmit={connect}>
                  <Field label="API key">
                    <input
                      value={apiKey}
                      onChange={event => setApiKey(event.target.value)}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder="Paste your API key"
                      minLength={16}
                      required
                    />
                  </Field>
                  <Field label="API secret" hint="Use a dedicated key with only the permissions this workstation needs.">
                    <input
                      type="password"
                      value={apiSecret}
                      onChange={event => setApiSecret(event.target.value)}
                      autoComplete="new-password"
                      placeholder="Paste your API secret"
                      minLength={24}
                      required
                    />
                  </Field>

                  {error && <InlineMessage tone="error">{error}</InlineMessage>}

                  {/*
                    The single premium treatment in the product, on the one
                    irreversible one-time action: attaching a live trading
                    credential. `silver` is used rather than the iridescent
                    preset because the design language reserves hue for meaning,
                    and neutral chrome introduces none.
                  */}
                  <MetalFx className="metal-cta" variant="button" preset="silver" theme="dark" strength={0.55} paused={busy}>
                    <button className="button primary block" disabled={busy}>
                      {busy
                        ? <><LoaderCircle className="spin" aria-hidden="true" /><Shimmer>Verifying trading access</Shimmer></>
                        : <><Zap aria-hidden="true" />Connect securely</>}
                    </button>
                  </MetalFx>
                </form>

                <p className="fine-print">
                  <AlertTriangle aria-hidden="true" />
                  Your Trade Cognition server IP must be included in the Delta API key allowlist.
                </p>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
