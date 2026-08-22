"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, BarChart3, CalendarClock, Check, KeyRound, LoaderCircle, LockKeyhole, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { errorMessage } from "@/lib/format";
import { normalizePhoneNumber } from "@/lib/auth-validation";
import {
  Brand, Field, InlineMessage, LearnMoreChevron, Segmented, Shimmer, useShake
} from "@/app/components/ui";

type Mode = "sign-in" | "sign-up" | "forgot-password";

/**
 * Unauthenticated entry point. The copy states only what the product actually
 * does — configure, schedule, review — because an operator deciding whether to
 * connect a live trading key needs facts, not persuasion.
 */
export default function AuthView({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState<"email" | "google" | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [shown, setShown] = useState(false);

  /**
   * A rejected credential shakes the card that owns the border, and the shake is
   * replayable: submitting the same wrong password twice shakes twice. The error
   * treatment and the shake are separate classes precisely so re-triggering one
   * does not flicker the other off and on in the same tick.
   */
  const { target: card, shake } = useShake<HTMLElement>();

  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(true));
    const url = new URL(window.location.href);
    let shouldReplaceUrl = false;
    if (url.searchParams.has("auth_error")) {
      setError("The sign-in link is invalid or has expired. Please try again.");
      url.searchParams.delete("auth_error");
      shouldReplaceUrl = true;
    }
    if (url.searchParams.get("reset") === "password") {
      setMode("forgot-password");
      url.searchParams.delete("reset");
      shouldReplaceUrl = true;
    }
    if (shouldReplaceUrl) window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    return () => cancelAnimationFrame(frame);
  }, []);

  function changeMode(next: string) {
    if (next !== "sign-in" && next !== "sign-up") return;
    setMode(next);
    setError("");
    setMessage("");
    setPhoneNumber("");
    setPassword("");
    setConfirmPassword("");
  }

  function showPasswordReset() {
    setMode("forgot-password");
    setError("");
    setMessage("");
    setPassword("");
    setConfirmPassword("");
  }

  function reject(text: string) {
    setError(text);
    shake();
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
      reject(errorMessage(nextError));
      setBusy(null);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    const normalizedPhone = mode === "sign-up" ? normalizePhoneNumber(phoneNumber) : null;
    if (mode === "sign-up" && !normalizedPhone) {
      reject("Enter a phone number with its country code, such as +91 98765 43210.");
      return;
    }
    if (mode === "sign-up" && password !== confirmPassword) {
      reject("The two passwords do not match.");
      return;
    }
    setBusy("email");
    try {
      const supabase = getSupabaseBrowserClient();
      if (mode === "forgot-password") {
        const redirectUrl = new URL("/auth/callback", window.location.origin);
        redirectUrl.searchParams.set("next", "/reset-password");
        const { error: authError } = await supabase.auth.resetPasswordForEmail(email.trim(), {
          redirectTo: redirectUrl.toString()
        });
        if (authError) throw authError;
        setMessage("If an account uses this email address, a password reset link is on its way.");
        return;
      }
      if (mode === "sign-in") {
        const { error: authError } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
        if (authError) throw authError;
        await onAuthenticated();
        return;
      }
      const { data, error: authError } = await supabase.auth.signUp({
        email: email.trim(),
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
          data: { phone_number: normalizedPhone }
        }
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
      reject(errorMessage(nextError));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="entry-shell">
      <header className="entry-bar">
        <Brand />
        <div className="entry-bar-actions">
          <Link className="button ghost t-learn" href="/market" aria-label="Public market analysis">
            <BarChart3 aria-hidden="true" />
            <span className="phone-label-wide" aria-hidden="true">Public market analysis</span>
            <span className="phone-label-compact" aria-hidden="true">Market</span>
            <LearnMoreChevron />
          </Link>
        </div>
      </header>

      <div className="entry-grid">
        {/* Stacked copy entering with rhythm: the eye lands on the eyebrow, then
            the headline, then the explanation, then the capability list. */}
        <section className={`entry-copy t-stagger${shown ? " is-shown" : ""}`}>
          <p className="eyebrow t-stagger-line t-stagger-line--1"><span aria-hidden="true" />Delta options trading</p>
          <h1 className="t-stagger-line t-stagger-line--2">Build, schedule, and track your options strategies.</h1>
          <p className="entry-lede t-stagger-line t-stagger-line--3">
            Plan multi-leg strategies for Delta Exchange India, choose entry and exit times, and
            keep a clear record of every scheduled strategy.
          </p>

          <ul className="entry-capabilities t-stagger-line t-stagger-line--4">
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
                <p>Orders are submitted only at the entry time you choose.</p>
              </div>
            </li>
            <li>
              <span aria-hidden="true"><ShieldCheck /></span>
              <div>
                <strong>Protected credentials</strong>
                <p>Your Delta API secret is encrypted and is never displayed after you connect.</p>
              </div>
            </li>
          </ul>
        </section>

        <div className="entry-card-shell">
          {/* `t-input` marks the element that owns the visible border, which is
              what the shake and the error border-colour tween act on. The message
              itself stays put in an `InlineMessage`, because a rejected
              credential has to remain readable rather than auto-reverting. */}
          <section
            className={`entry-card t-input${error ? " is-error" : ""}`}
            ref={card}
            aria-labelledby="auth-card-title"
          >
            <header className="entry-card-head">
              <span className="panel-icon" aria-hidden="true"><LockKeyhole /></span>
              <div>
                <h2 id="auth-card-title">
                  {mode === "sign-in" ? "Sign in" : mode === "sign-up" ? "Create your account" : "Reset your password"}
                </h2>
                <p>
                  {mode === "sign-in"
                    ? "Use the email and password for your Trade Cognition account."
                    : mode === "sign-up"
                      ? "Create an account to save and manage your strategies."
                      : "Enter your account email and we will send a secure reset link."}
                </p>
              </div>
            </header>

            {mode === "forgot-password" ? (
              <button type="button" className="auth-text-button auth-text-button--back" onClick={() => changeMode("sign-in")}>
                <ArrowLeft aria-hidden="true" />Back to sign in
              </button>
            ) : (
              <Segmented
                label="Account"
                value={mode}
                onChange={changeMode}
                options={[{ value: "sign-in", label: "Sign in" }, { value: "sign-up", label: "Create account" }]}
              />
            )}

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
              {mode === "sign-up" && (
                <Field label="Phone number" hint="Include the country code, for example +91 98765 43210.">
                  <input
                    type="tel"
                    value={phoneNumber}
                    onChange={event => setPhoneNumber(event.target.value)}
                    autoComplete="tel"
                    inputMode="tel"
                    placeholder="+91 98765 43210"
                    required
                  />
                </Field>
              )}
              {mode !== "forgot-password" && (
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
              )}
              {mode === "sign-in" && (
                <button type="button" className="auth-text-button" onClick={showPasswordReset}>
                  Forgot password?
                </button>
              )}
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
                  ? <><LoaderCircle className="spin" aria-hidden="true" /><Shimmer>{mode === "sign-in" ? "Signing in" : mode === "sign-up" ? "Creating account" : "Sending reset link"}</Shimmer></>
                  : <>{mode === "sign-in"
                    ? <><LockKeyhole aria-hidden="true" />Sign in</>
                    : mode === "sign-up"
                      ? <><Check aria-hidden="true" />Create account</>
                      : <><KeyRound aria-hidden="true" />Send reset link</>}</>}
              </button>
            </form>

            {mode !== "forgot-password" && (
              <>
                <div className="divider"><span>or</span></div>

                <button type="button" className="button google block" onClick={() => void signInWithGoogle()} disabled={busy !== null}>
                  <span className="google-mark" aria-hidden="true">G</span>
                  {busy === "google" ? <Shimmer>Opening Google</Shimmer> : "Continue with Google"}
                </button>
              </>
            )}

            <p className="fine-print">
              <AlertTriangle aria-hidden="true" />
              {mode === "forgot-password"
                ? "The reset link can be used once and expires after a limited time."
                : "Signing in does not connect your Delta account. You will connect it separately after authentication."}
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
