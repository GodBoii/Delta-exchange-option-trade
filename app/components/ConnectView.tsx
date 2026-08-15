"use client";

import { useState } from "react";
import { AlertTriangle, KeyRound, LoaderCircle, LockKeyhole, ShieldCheck, Zap } from "lucide-react";
import { requestJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import type { Account, AppUser } from "@/lib/app-types";
import { Brand, Field, InlineMessage } from "@/app/components/ui";

/**
 * One-time Delta Exchange connection.
 *
 * This is the only screen where a live trading credential is entered, so it
 * states plainly what happens to the secret and what does not happen on submit
 * (no order is placed). Trading access is verified server-side before the
 * connection is stored.
 */
export default function ConnectView({ user, onConnected, onSignOut }: {
  user: AppUser;
  onConnected: (account: Account) => void;
  onSignOut: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
      onConnected(result.account);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="entry-shell">
      <header className="entry-bar">
        <Brand />
        <div className="entry-bar-actions">
          <span className="entry-user">{user.displayName || user.email}</span>
          <button type="button" className="button ghost" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      <div className="entry-grid">
        <section className="entry-copy">
          <p className="eyebrow"><span aria-hidden="true" />One-time setup</p>
          <h1>Connect Delta Exchange to enable execution.</h1>
          <p className="entry-lede">
            Your workspace account is ready. This connection lets the backend resolve live option
            contracts and submit the strategies you schedule.
          </p>

          <dl className="entry-facts">
            <div>
              <dt><ShieldCheck aria-hidden="true" />Stored in Supabase Vault</dt>
              <dd>The API secret is encrypted at rest and is never returned to the browser.</dd>
            </div>
            <div>
              <dt><LockKeyhole aria-hidden="true" />Verified before saving</dt>
              <dd>Trading permission is checked against Delta before the connection is accepted.</dd>
            </div>
            <div>
              <dt><KeyRound aria-hidden="true" />Revocable at any time</dt>
              <dd>Disconnecting deletes the stored secret and leaves your saved strategies intact.</dd>
            </div>
          </dl>
        </section>

        <section className="entry-card">
          <header className="entry-card-head">
            <span className="panel-icon" aria-hidden="true"><KeyRound /></span>
            <div>
              <h2>Delta Exchange India</h2>
              <p>Live production account.</p>
            </div>
          </header>

          <InlineMessage tone="warning">
            This is a live venue. Connecting does not place an order; execution starts only from a
            strategy you schedule.
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

            <button className="button primary block" disabled={busy}>
              {busy
                ? <><LoaderCircle className="spin" aria-hidden="true" />Verifying trading access</>
                : <><Zap aria-hidden="true" />Connect securely</>}
            </button>
          </form>

          <p className="fine-print">
            <AlertTriangle aria-hidden="true" />
            The static public IP of the backend server must be allowlisted on the Delta API key.
          </p>
        </section>
      </div>
    </div>
  );
}
