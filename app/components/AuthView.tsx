"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, BarChart3, CalendarClock, Check, LoaderCircle, LockKeyhole, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { errorMessage } from "@/lib/format";
import { Brand, Field, InlineMessage, Segmented } from "@/app/components/ui";

type Mode = "sign-in" | "sign-up";

/**
 * Unauthenticated entry point. The copy states only what the product actually
 * does — configure, schedule, review — because an operator deciding whether to
 * connect a live trading key needs facts, not persuasion.
 */
export default function AuthView({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState<"email" | "google" | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  function changeMode(next: string) {
    setMode(next as Mode);
    setError("");
    setMessage("");
    setPassword("");
    setConfirmPassword("");
  }

  async function signInWithGoogle() {
    setBusy("google");
    setError("");
    setMessage("");
    try {
      const { error: authError } = await getSupabaseBrowserClient().auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: `${window.location.origin}/auth/callback` }
      });
      if (authError) throw authError;
    } catch (nextError) {
      setError(errorMessage(nextError));
      setBusy(null);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (mode === "sign-up" && password !== confirmPassword) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy("email");
    try {
      const supabase = getSupabaseBrowserClient();
      if (mode === "sign-in") {
        const { error: authError } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
        if (authError) throw authError;
        await onAuthenticated();
        return;
      }
      const { data, error: authError } = await supabase.auth.signUp({
        email: email.trim(),
        password,
        options: { emailRedirectTo: `${window.location.origin}/auth/callback` }
      });
      if (authError) throw authError;
      if (data.session) {
        await onAuthenticated();
        return;
      }
      setPassword("");
      setConfirmPassword("");
      setMessage("Account created. Confirm the address from your email to continue.");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="entry-shell">
      <header className="entry-bar">
        <Brand />
        <div className="entry-bar-actions">
          <Link className="button ghost" href="/market"><BarChart3 aria-hidden="true" />Public market analysis</Link>
        </div>
      </header>

      <div className="entry-grid">
        <section className="entry-copy">
          <p className="eyebrow"><span aria-hidden="true" />Options strategy operations</p>
          <h1>Build once, schedule precisely, review every run.</h1>
          <p className="entry-lede">
            A private workspace for Delta Exchange India option strategies. Configure multi-leg
            structures, hand them to a server-side scheduler, and keep an auditable record of
            what was submitted.
          </p>

          <ul className="entry-capabilities">
            <li>
              <span aria-hidden="true"><SlidersHorizontal /></span>
              <div>
                <strong>Multi-leg configuration</strong>
                <p>Up to twelve legs with per-leg or combined-premium risk control.</p>
              </div>
            </li>
            <li>
              <span aria-hidden="true"><CalendarClock /></span>
              <div>
                <strong>Scheduled entry and exit</strong>
                <p>Orders leave only at the configured time, from the backend, never the browser.</p>
              </div>
            </li>
            <li>
              <span aria-hidden="true"><ShieldCheck /></span>
              <div>
                <strong>Credentials stay server-side</strong>
                <p>The Delta API secret is held in Supabase Vault and never returns to the client.</p>
              </div>
            </li>
          </ul>
        </section>

        <section className="entry-card" aria-labelledby="auth-card-title">
          <header className="entry-card-head">
            <span className="panel-icon" aria-hidden="true"><LockKeyhole /></span>
            <div>
              <h2 id="auth-card-title">{mode === "sign-in" ? "Sign in" : "Create your workspace"}</h2>
              <p>{mode === "sign-in" ? "Use the email and password for your workspace account." : "Accounts are managed by Supabase Auth."}</p>
            </div>
          </header>

          <Segmented
            label="Account"
            value={mode}
            onChange={changeMode}
            options={[{ value: "sign-in", label: "Sign in" }, { value: "sign-up", label: "Create account" }]}
          />

          <form className="stack" onSubmit={submit}>
            <Field label="Email address">
              <input
                type="email"
                value={email}
                onChange={event => setEmail(event.target.value)}
                autoComplete="email"
                placeholder="you@company.com"
                required
              />
            </Field>
            <Field label="Password" hint={mode === "sign-up" ? "At least 8 characters." : undefined}>
              <input
                type="password"
                value={password}
                onChange={event => setPassword(event.target.value)}
                autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
                minLength={8}
                required
              />
            </Field>
            {mode === "sign-up" && (
              <Field label="Confirm password">
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={event => setConfirmPassword(event.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                  required
                />
              </Field>
            )}

            {error && <InlineMessage tone="error">{error}</InlineMessage>}
            {message && <InlineMessage tone="ok">{message}</InlineMessage>}

            <button className="button primary block" disabled={busy !== null}>
              {busy === "email"
                ? <><LoaderCircle className="spin" aria-hidden="true" />{mode === "sign-in" ? "Signing in" : "Creating account"}</>
                : <>{mode === "sign-in" ? <><LockKeyhole aria-hidden="true" />Sign in</> : <><Check aria-hidden="true" />Create account</>}</>}
            </button>
          </form>

          <div className="divider"><span>or</span></div>

          <button type="button" className="button google block" onClick={() => void signInWithGoogle()} disabled={busy !== null}>
            <span className="google-mark" aria-hidden="true">G</span>
            {busy === "google" ? "Opening Google" : "Continue with Google"}
          </button>

          <p className="fine-print">
            <AlertTriangle aria-hidden="true" />
            Signing in creates the workspace account only. Delta Exchange is connected separately,
            once, after authentication.
          </p>
        </section>
      </div>
    </div>
  );
}
