"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, KeyRound, LoaderCircle, LockKeyhole, ShieldCheck } from "@/app/components/icons";
import { Brand, Field, InlineMessage, Shimmer } from "@/app/components/ui";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { errorMessage } from "@/lib/format";

type ResetStatus = "checking" | "editing" | "saving" | "success" | "invalid";

export default function ResetPasswordPage() {
  const [status, setStatus] = useState<ResetStatus>("checking");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const url = new URL(window.location.href);
    if (url.searchParams.has("auth_error")) {
      setStatus("invalid");
      return () => { cancelled = true; };
    }

    void getSupabaseBrowserClient().auth.getUser().then(({ data, error: authError }) => {
      if (cancelled) return;
      setStatus(authError || !data.user ? "invalid" : "editing");
    });

    return () => { cancelled = true; };
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (password !== confirmPassword) {
      setError("The two passwords do not match.");
      return;
    }

    setStatus("saving");
    try {
      const { error: authError } = await getSupabaseBrowserClient().auth.updateUser({ password });
      if (authError) throw authError;
      setPassword("");
      setConfirmPassword("");
      setStatus("success");
    } catch (nextError) {
      setError(errorMessage(nextError));
      setStatus("editing");
    }
  }

  return (
    <div className="entry-shell">
      <header className="entry-bar"><Brand /></header>

      <main className="entry-grid">
        <section className="entry-copy t-stagger is-shown">
          <p className="eyebrow t-stagger-line t-stagger-line--1"><span aria-hidden="true" />Account recovery</p>
          <h1 className="t-stagger-line t-stagger-line--2">Choose a new account password.</h1>
          <p className="entry-lede t-stagger-line t-stagger-line--3">
            The recovery link signs you in for this password change. Use a new password that you do
            not reuse for email, Delta Exchange, or another service.
          </p>
        </section>

        <div className="entry-card-shell">
          <section className={`entry-card${error ? " is-error" : ""}`} aria-labelledby="reset-card-title">
            <header className="entry-card-head">
              <span className="panel-icon" aria-hidden="true"><LockKeyhole /></span>
              <div>
                <h2 id="reset-card-title">Set a new password</h2>
                <p>Your password must contain at least 8 characters.</p>
              </div>
            </header>

            {status === "checking" && (
              <InlineMessage tone="info"><LoaderCircle className="spin" aria-hidden="true" />Checking your recovery link.</InlineMessage>
            )}

            {status === "invalid" && (
              <div className="stack">
                <InlineMessage tone="error">This password reset link is invalid or has expired.</InlineMessage>
                <Link className="button secondary block" href="/?reset=password"><KeyRound aria-hidden="true" />Request a new link</Link>
              </div>
            )}

            {(status === "editing" || status === "saving") && (
              <form className="stack" onSubmit={submit}>
                <Field label="New password" hint="At least 8 characters.">
                  <input
                    type="password"
                    value={password}
                    onChange={event => setPassword(event.target.value)}
                    autoComplete="new-password"
                    minLength={8}
                    required
                  />
                </Field>
                <Field label="Confirm new password">
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={event => setConfirmPassword(event.target.value)}
                    autoComplete="new-password"
                    minLength={8}
                    required
                  />
                </Field>
                {error && <InlineMessage tone="error">{error}</InlineMessage>}
                <button className="button primary block" disabled={status === "saving"}>
                  {status === "saving"
                    ? <><LoaderCircle className="spin" aria-hidden="true" /><Shimmer>Updating password</Shimmer></>
                    : <><ShieldCheck aria-hidden="true" />Update password</>}
                </button>
              </form>
            )}

            {status === "success" && (
              <div className="stack">
                <InlineMessage tone="ok">Your password has been updated.</InlineMessage>
                <Link className="button primary block" href="/"><Check aria-hidden="true" />Continue to dashboard</Link>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
