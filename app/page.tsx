"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import Link from "next/link";
import {
  Activity, AlertTriangle, ArrowDown, ArrowUp, BarChart3, Bell, Check, ChevronDown,
  CircleDollarSign, CircleStop, Clock3, Copy, Download, GripVertical, KeyRound, Layers3,
  LayoutDashboard, LoaderCircle, LockKeyhole, LogOut, Menu, Plus, RefreshCw,
  Shield, ShieldCheck, Trash2, TrendingUp, Upload, Wallet,
  WifiOff, X, Zap
} from "lucide-react";
import type { StrategyDefinition, StrategyLeg } from "@/lib/strategy-types";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import BtcMarketChart from "@/app/components/BtcMarketChart";

type Account = { id: string; accountName?: string | null; email?: string | null; environment: "production" };
type AppUser = { id: string; email?: string | null; displayName?: string | null; avatarUrl?: string | null };
type SessionResponse = { success: boolean; authenticated: boolean; connected: boolean; user: AppUser | null; account: Account | null; message?: string; error?: string };
type StrategyRow = { id: string; name: string; status: string; entryAt: string; exitAt: string; entryExecutedAt?: string | null; lastError?: string | null; createdAt: string };
type RiskStrategy = { id: string; name: string; status: string; riskState: Record<string, unknown>; monitoredAt?: string | null; triggeredAt?: string | null };
type Overview = { balances: unknown[]; orders: Record<string, unknown>[]; positions: Record<string, unknown>[]; riskStrategies: RiskStrategy[] };
type Tab = "builder" | "market" | "dashboard" | "runs";
type BackendStatus = "checking" | "online" | "offline";

const today = () => new Date(Date.now() + 86400000).toISOString().slice(0, 10);
const localDateTime = (offsetHours: number) => {
  const date = new Date(Date.now() + offsetHours * 3600000);
  date.setMinutes(Math.ceil(date.getMinutes() / 5) * 5, 0, 0);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const iso = (value: string) => value ? new Date(value).toISOString() : "";
const uid = () => globalThis.crypto?.randomUUID?.().slice(0, 12) ?? Math.random().toString(36).slice(2, 14);

const newLeg = (overrides: Partial<StrategyLeg> = {}): StrategyLeg => ({
  id: uid(), lots: 1, position: "buy", optionType: "call", expiry: today(), strikeMode: "atm",
  strikeSteps: 0, orderType: "market_order", reentryOnTarget: 0, reentryOnStop: 0, ...overrides
});

const initialStrategy = (): StrategyDefinition => ({
  name: "BTC ATM short straddle",
  instrument: { index: "BTCUSD", underlying: "BTC", underlyingFrom: "cash" },
  entry: { strategyType: "intraday", entryAt: iso(localDateTime(1)), exitAt: iso(localDateTime(8)) },
  squareOff: "complete", riskMode: "combined_premium", combinedStopLossPercent: 100,
  emergencyStopLossPercent: 300, trailToBreakEven: false, breakEvenScope: "all_legs",
  legs: [
    newLeg({ position: "sell", optionType: "call", strikeMode: "atm" }),
    newLeg({ position: "sell", optionType: "put", strikeMode: "atm" })
  ],
  acknowledgement: true
});
const DRAFT_STORAGE_KEY = "delta-strategy-draft-v1";

function isStrategyDefinition(value: unknown): value is StrategyDefinition {
  if (!value || typeof value !== "object") return false;
  const strategy = value as Record<string, unknown>;
  return typeof strategy.name === "string"
    && Boolean(strategy.instrument && typeof strategy.instrument === "object")
    && Boolean(strategy.entry && typeof strategy.entry === "object")
    && Array.isArray(strategy.legs)
    && strategy.legs.length > 0;
}

function hydrateStrategy(strategy: StrategyDefinition): StrategyDefinition {
  return {
    ...strategy,
    acknowledgement: true,
    riskMode: strategy.riskMode ?? "legwise",
    combinedStopLossPercent: strategy.combinedStopLossPercent ?? undefined,
    emergencyStopLossPercent: strategy.emergencyStopLossPercent ?? undefined
  };
}

function strategyValidation(strategy: StrategyDefinition) {
  const fields = new Set<string>();
  let firstLegId: string | null = null;
  if (strategy.name.trim().length < 2) fields.add("name");
  const entryAt = new Date(strategy.entry.entryAt).getTime();
  const exitAt = new Date(strategy.entry.exitAt).getTime();
  if (!Number.isFinite(entryAt)) fields.add("entryAt");
  if (!Number.isFinite(exitAt) || (Number.isFinite(entryAt) && exitAt <= entryAt)) fields.add("exitAt");
  if (!strategy.legs.length) fields.add("legs");

  for (const leg of strategy.legs) {
    const mark = (field: string) => { fields.add(`leg.${leg.id}.${field}`); firstLegId ??= leg.id; };
    if (!Number.isFinite(leg.lots) || leg.lots < 1) mark("lots");
    if (!leg.expiry || Number.isNaN(new Date(`${leg.expiry}T00:00:00`).getTime())) mark("expiry");
    if (leg.strikeMode === "exact" && (!leg.exactStrike || leg.exactStrike <= 0)) mark("exactStrike");
    if (leg.orderType === "limit_order" && (!leg.limitPrice || !/^\d+(\.\d+)?$/.test(leg.limitPrice))) mark("limitPrice");
    if (strategy.riskMode === "legwise" && leg.position === "sell" && (!leg.stopLoss || leg.stopLoss <= 0)) mark("stopLoss");
  }

  if (strategy.riskMode === "combined_premium") {
    if (!strategy.combinedStopLossPercent || strategy.combinedStopLossPercent <= 0) fields.add("combinedStopLossPercent");
    if (strategy.legs.filter(leg => leg.position === "sell").length < 2) fields.add("riskMode");
  }
  return { fields, firstLegId };
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

let resolvedApiOrigin: string | null = null;
let apiResolution: Promise<string> | null = null;

function resetApiOrigin() {
  resolvedApiOrigin = null;
  apiResolution = null;
}

async function discoverApiOrigin() {
  const explicit = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");
  const ports = (process.env.NEXT_PUBLIC_API_PORTS ?? "8000,8585,8085,8011,8001")
    .split(",").map(port => port.trim()).filter(port => /^\d{2,5}$/.test(port));
  const localHost = ["localhost", "127.0.0.1"].includes(window.location.hostname) ? window.location.hostname : "localhost";
  const candidates = Array.from(new Set([
    ...(explicit ? [explicit] : []),
    ...ports.map(port => `http://${localHost}:${port}`)
  ])).filter(origin => window.location.protocol !== "https:" || origin.startsWith("https://"));
  if (!candidates.length) throw new Error("The trading backend is not configured for this website.");

  const probes = candidates.map(async origin => {
    const response = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(1_200), cache: "no-store" });
    if (!response.ok) throw new Error(`${origin} is unavailable`);
    const health = await response.json().catch(() => null) as { success?: boolean; service?: string } | null;
    if (!health?.success || health.service !== "delta-strategy-api") throw new Error(`${origin} is not the trading API`);
    return origin;
  });
  try { return await Promise.any(probes); }
  catch { throw new Error("No local trading backend was found on the configured ports."); }
}

async function apiOrigin() {
  if (resolvedApiOrigin) return resolvedApiOrigin;
  apiResolution ??= discoverApiOrigin();
  try {
    resolvedApiOrigin = await apiResolution;
    return resolvedApiOrigin;
  } finally { apiResolution = null; }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const origin = await apiOrigin();
  const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
  const response = await fetch(`${origin}${url}`, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(8_000),
    headers: {
      "Content-Type": "application/json",
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
      ...init?.headers
    }
  });
  const data = await response.json().catch(() => ({})) as T & { message?: string; error?: string | { message?: string; code?: string } };
  if (!response.ok) {
    const nested = typeof data.error === "object" ? data.error?.message : data.error;
    throw new Error(data.message || nested || `Request failed (${response.status})`);
  }
  return data;
}

export default function Home() {
  const root = useRef<HTMLDivElement>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [user, setUser] = useState<AppUser | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");
  const [tab, setTab] = useState<Tab>("builder");
  const [mobileNav, setMobileNav] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  useGSAP(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.from("[data-reveal]", { opacity: 0, y: 14, duration: .55, stagger: .06, ease: "power3.out" });
  }, { scope: root, dependencies: [account?.id, tab] });

  const loadSession = useCallback(async () => {
    try {
      const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
      if (!session?.user) {
        setUser(null); setAccount(null); setBackendStatus("offline");
        return;
      }
      const metadata = session.user.user_metadata ?? {};
      setUser({
        id: session.user.id,
        email: session.user.email,
        displayName: String(metadata.full_name ?? metadata.name ?? session.user.email?.split("@")[0] ?? "Client"),
        avatarUrl: typeof metadata.avatar_url === "string" ? metadata.avatar_url : null
      });
      try {
        resetApiOrigin();
        const data = await requestJson<SessionResponse>("/api/session", { signal: AbortSignal.timeout(3_000) });
        setBackendStatus("online");
        setAccount(data.connected ? data.account : null);
        if (data.user) setUser(data.user);
      } catch {
        setBackendStatus("offline");
        setAccount(null);
      }
    } catch (error) { setNotice({ tone: "error", text: errorMessage(error) }); }
    finally { setSessionLoading(false); }
  }, []);

  useEffect(() => { void loadSession(); }, [loadSession]);

  async function disconnectDelta() {
    try {
      await requestJson("/api/session", { method: "DELETE" });
      setAccount(null); setConfirmDisconnect(false); setTab("builder"); setNotice({ tone: "ok", text: "Delta Exchange disconnected. Your application account remains signed in." });
    } catch (error) { setNotice({ tone: "error", text: errorMessage(error) }); }
  }

  async function signOut() {
    try {
      await getSupabaseBrowserClient().auth.signOut();
      setUser(null); setAccount(null); setBackendStatus("offline"); setTab("builder");
    } catch (error) { setNotice({ tone: "error", text: errorMessage(error) }); }
  }

  if (sessionLoading) return <LoadingScreen />;

  return (
    <div className="app" ref={root}>
      {account ? (
        <>
          <header className="topbar" data-reveal>
            <Brand />
            <nav className={mobileNav ? "main-nav open" : "main-nav"} aria-label="Primary navigation">
              <NavButton active={tab === "builder"} icon={<Layers3 />} onClick={() => { setTab("builder"); setMobileNav(false); }}>Strategy builder</NavButton>
              <NavButton active={tab === "market"} icon={<BarChart3 />} onClick={() => { setTab("market"); setMobileNav(false); }}>Market analysis</NavButton>
              <NavButton active={tab === "dashboard"} icon={<LayoutDashboard />} onClick={() => { setTab("dashboard"); setMobileNav(false); }}>Dashboard</NavButton>
              <NavButton active={tab === "runs"} icon={<Activity />} onClick={() => { setTab("runs"); setMobileNav(false); }}>Run history</NavButton>
            </nav>
            <div className="account-cluster">
              <span className="connection-chip"><i />Delta connected</span>
              <button className="icon-button notification" aria-label="Notifications"><Bell /><span /></button>
              <div className="account-copy"><strong>{account.accountName || "Delta account"}</strong><span>{account.email || "Connected securely"}</span></div>
              <button className="icon-button" onClick={() => setConfirmDisconnect(true)} aria-label="Disconnect Delta Exchange" title="Disconnect Delta Exchange"><KeyRound /></button>
              <button className="icon-button" onClick={signOut} aria-label="Sign out" title="Sign out"><LogOut /></button>
              <button className="icon-button nav-toggle" onClick={() => setMobileNav(v => !v)} aria-label="Toggle navigation"><Menu /></button>
            </div>
          </header>
          <main className="workspace">
            {notice && <Toast tone={notice.tone} onClose={() => setNotice(null)}>{notice.text}</Toast>}
            {tab === "builder" && <StrategyBuilder onNotice={setNotice} liveEnabled />}
            {tab === "market" && <BtcMarketChart />}
            {tab === "dashboard" && <Dashboard onNotice={setNotice} />}
            {tab === "runs" && <RunHistory onNotice={setNotice} />}
          </main>
          {confirmDisconnect && <ConfirmModal title="Disconnect Delta Exchange?" description="The stored Delta API secret will be permanently removed from Vault. Your application account and saved strategy history will remain." confirm="Disconnect Delta" onClose={() => setConfirmDisconnect(false)} onConfirm={() => void disconnectDelta()} />}
        </>
      ) : user && backendStatus === "online" ? <ConnectView user={user} onSignOut={signOut} onConnected={(next) => { setAccount(next); setNotice({ tone: "ok", text: "Delta Exchange connected securely." }); }} />
        : user ? <DesignWorkspace user={user} notice={notice} onNotice={setNotice} onClearNotice={() => setNotice(null)} onRetry={loadSession} onSignOut={signOut} />
        : <AuthView onAuthenticated={loadSession} />}
    </div>
  );
}

function AuthView({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const panel = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState<"email" | "google" | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useGSAP(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.from(".auth-copy > *, .auth-card", { opacity: 0, y: 22, stagger: .08, duration: .65, ease: "power3.out" });
  }, { scope: panel });

  async function signInWithGoogle() {
    setBusy("google"); setError(""); setMessage("");
    try {
      const origin = window.location.origin;
      const { error: authError } = await getSupabaseBrowserClient().auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: `${origin}/auth/callback` }
      });
      if (authError) throw authError;
    } catch (nextError) {
      setError(errorMessage(nextError)); setBusy(null);
    }
  }

  async function submitEmailAuth(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(""); setMessage("");
    if (mode === "sign-up" && password !== confirmPassword) {
      setError("Passwords do not match.");
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
      setPassword(""); setConfirmPassword("");
      setMessage("Account created. Check your email and confirm the address to continue.");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(null);
    }
  }

  function changeMode(nextMode: "sign-in" | "sign-up") {
    setMode(nextMode); setError(""); setMessage(""); setPassword(""); setConfirmPassword("");
  }

  return <main className="connect-shell auth-shell" ref={panel}>
    <header className="connect-header"><Brand /><div className="auth-header-actions"><Link href="/market"><BarChart3 /> Market analysis</Link><span className="auth-trust"><ShieldCheck /> Secure client access</span></div></header>
    <div className="auth-grid">
      <section className="auth-copy">
        <div className="eyebrow"><span /> Professional strategy operations</div>
        <h1>One account for every strategy decision.</h1>
        <p>Sign in to build, schedule, and monitor Delta Exchange option strategies from a private workspace.</p>
        <div className="trust-row"><div><ShieldCheck /><span><strong>Private workspace</strong><small>Your strategies are isolated to your account.</small></span></div><div><LockKeyhole /><span><strong>Persistent sign-in</strong><small>Secure sessions are managed by Supabase Auth.</small></span></div></div>
      </section>
      <section className="auth-card" aria-label="Sign in">
        <div className="card-heading"><span className="heading-icon"><LockKeyhole /></span><div><h2>{mode === "sign-in" ? "Welcome back" : "Create your workspace"}</h2><p>{mode === "sign-in" ? "Sign in with your email and password." : "Create a secure account managed by Supabase."}</p></div></div>
        <div className="auth-mode" role="tablist" aria-label="Authentication mode">
          <button type="button" role="tab" aria-selected={mode === "sign-in"} className={mode === "sign-in" ? "active" : ""} onClick={() => changeMode("sign-in")}>Sign in</button>
          <button type="button" role="tab" aria-selected={mode === "sign-up"} className={mode === "sign-up" ? "active" : ""} onClick={() => changeMode("sign-up")}>Create account</button>
        </div>
        <form className="email-auth-form" onSubmit={submitEmailAuth}>
          <Field label="Email address"><input type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email" placeholder="you@company.com" required /></Field>
          <Field label="Password"><input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete={mode === "sign-in" ? "current-password" : "new-password"} placeholder={mode === "sign-in" ? "Enter your password" : "At least 8 characters"} minLength={8} required /></Field>
          {mode === "sign-up" && <Field label="Confirm password"><input type="password" value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} autoComplete="new-password" placeholder="Enter the password again" minLength={8} required /></Field>}
          {error && <div className="inline-error" role="alert"><AlertTriangle />{error}</div>}
          {message && <div className="inline-success" role="status"><Check />{message}</div>}
          <button className="primary-button full" disabled={busy !== null}>{busy === "email" ? <><LoaderCircle className="spin" />{mode === "sign-in" ? "Signing in" : "Creating account"}</> : <><LockKeyhole />{mode === "sign-in" ? "Sign in securely" : "Create account"}</>}</button>
        </form>
        <div className="auth-divider"><span>Or continue with</span></div>
        <button className="google-button" onClick={() => void signInWithGoogle()} disabled={busy !== null}><span className="google-mark">G</span>{busy === "google" ? "Opening Google" : "Continue with Google"}</button>
        <p className="terms">Signing in creates your private application account. Delta Exchange is connected separately once, after authentication.</p>
      </section>
    </div>
  </main>;
}

function Brand() {
  return <div className="brand" aria-label="Trade Cognition"><span className="brand-mark"><i /><i /><i /></span><span><strong>Trade Cognition</strong><small>Delta workspace</small></span></div>;
}

function LoadingScreen() {
  return <div className="loading-screen"><Brand /><LoaderCircle className="spin" /><span>Restoring secure session</span></div>;
}

function NavButton({ active, icon, onClick, children }: { active: boolean; icon: React.ReactNode; onClick: () => void; children: React.ReactNode }) {
  return <button className={active ? "nav-button active" : "nav-button"} onClick={onClick}>{icon}<span>{children}</span></button>;
}

function Toast({ tone, onClose, children }: { tone: "ok" | "error"; onClose: () => void; children: React.ReactNode }) {
  return <div className={`toast ${tone}`} role="status">{tone === "ok" ? <Check /> : <AlertTriangle />}<span>{children}</span><button onClick={onClose} aria-label="Dismiss"><X /></button></div>;
}

function DesignWorkspace({ user, notice, onNotice, onClearNotice, onRetry, onSignOut }: {
  user: AppUser;
  notice: { tone: "ok" | "error"; text: string } | null;
  onNotice: (notice: { tone: "ok" | "error"; text: string }) => void;
  onClearNotice: () => void;
  onRetry: () => Promise<void>;
  onSignOut: () => void;
}) {
  return <>
    <header className="topbar design-topbar" data-reveal>
      <Brand />
      <nav className="main-nav" aria-label="Design workspace navigation"><NavButton active icon={<Layers3 />} onClick={() => undefined}>Strategy designer</NavButton></nav>
      <div className="account-cluster">
        <span className="connection-chip offline"><i />Design mode</span>
        <div className="account-copy"><strong>{user.displayName || "Client"}</strong><span>Frontend-only workspace</span></div>
        <button className="icon-button" onClick={onSignOut} aria-label="Sign out" title="Sign out"><LogOut /></button>
      </div>
    </header>
    <main className="workspace">
      {notice && <Toast tone={notice.tone} onClose={onClearNotice}>{notice.text}</Toast>}
      <section className="backend-banner" role="status"><WifiOff /><div><strong>Trading backend is not connected</strong><p>You can design and export strategies here. Start Docker and open the local frontend to resolve contracts, connect Delta, schedule, or execute.</p></div><button className="secondary-button" onClick={() => void onRetry()}><RefreshCw />Retry connection</button></section>
      <StrategyBuilder onNotice={onNotice} liveEnabled={false} />
    </main>
  </>;
}

function ConnectView({ user, onConnected, onSignOut }: { user: AppUser; onConnected: (account: Account) => void; onSignOut: () => void }) {
  const panel = useRef<HTMLDivElement>(null);
  const [apiKey, setApiKey] = useState(""); const [apiSecret, setApiSecret] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useGSAP(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.from(".connect-copy > *, .connect-card", { opacity: 0, y: 24, stagger: .08, duration: .7, ease: "power3.out" });
  }, { scope: panel });

  async function connect(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await requestJson<{ account: Account }>("/api/session/connect", { method: "POST", body: JSON.stringify({ apiKey, apiSecret }) });
      setApiKey(""); setApiSecret(""); onConnected(result.account);
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  return <main className="connect-shell" ref={panel}>
    <header className="connect-header"><Brand /><div className="onboarding-user"><span>{user.displayName || user.email}</span><button onClick={onSignOut}>Sign out</button></div></header>
    <div className="connect-grid">
      <section className="connect-copy">
        <div className="eyebrow"><span /> Strategy operations console</div>
        <h1>Connect Delta Exchange once.</h1>
        <p>Your application account is ready. Complete this one-time connection to resolve live contracts and securely submit reviewed strategies.</p>
        <div className="trust-row"><div><ShieldCheck /><span><strong>Encrypted at rest</strong><small>Your API secret never returns to the browser.</small></span></div><div><LockKeyhole /><span><strong>Persistent secure session</strong><small>Reconnect only when you choose to sign out.</small></span></div></div>
      </section>
      <form className="connect-card" onSubmit={connect} aria-label="Connect Delta Exchange account">
        <div className="card-heading"><span className="heading-icon"><KeyRound /></span><div><h2>Connect Delta Exchange</h2><p>Verify trading access to continue.</p></div></div>
        <div className="production-warning"><AlertTriangle /><p><strong>Live Delta Exchange India connection.</strong><br />Orders are submitted when you choose Execute strategy.</p></div>
        <Field label="API key"><input value={apiKey} onChange={e => setApiKey(e.target.value)} autoComplete="off" spellCheck={false} placeholder="Paste your API key" minLength={16} required /></Field>
        <Field label="API secret"><input type="password" value={apiSecret} onChange={e => setApiSecret(e.target.value)} autoComplete="new-password" placeholder="Paste your API secret" minLength={24} required /></Field>
        {error && <div className="inline-error" role="alert"><AlertTriangle />{error}</div>}
        <div className="security-note"><ShieldCheck /><p><strong>Trading access is verified before connection.</strong><br />Use a dedicated key with only the permissions this workstation needs.</p></div>
        <button className="primary-button full" disabled={busy}>{busy ? <><LoaderCircle className="spin" />Verifying connection</> : <><Zap />Connect securely</>}</button>
        <p className="terms">Connecting does not place an order. Execution begins only from the strategy workspace.</p>
      </form>
    </div>
  </main>;
}

function StrategyBuilder({ onNotice, liveEnabled }: { onNotice: (n: { tone: "ok" | "error"; text: string }) => void; liveEnabled: boolean }) {
  const [strategy, setStrategy] = useState<StrategyDefinition>(initialStrategy);
  const [expanded, setExpanded] = useState<string | null>(strategy.legs[0].id);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState("");
  const [invalidFields, setInvalidFields] = useState<Set<string>>(new Set());
  const [draftReady, setDraftReady] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved) as unknown;
        if (isStrategyDefinition(parsed)) {
          setStrategy(hydrateStrategy(parsed));
          setExpanded(parsed.legs[0]?.id ?? null);
        }
      }
    } catch { localStorage.removeItem(DRAFT_STORAGE_KEY); }
    finally { setDraftReady(true); }
  }, []);

  useEffect(() => {
    if (draftReady) localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(strategy));
  }, [draftReady, strategy]);

  useEffect(() => {
    if (invalidFields.size) setInvalidFields(strategyValidation(strategy).fields);
  }, [strategy, invalidFields.size]);

  const updateLeg = (id: string, patch: Partial<StrategyLeg>) => setStrategy(s => ({ ...s, legs: s.legs.map(l => l.id === id ? { ...l, ...patch } : l) }));
  const removeLeg = (id: string) => setStrategy(s => ({ ...s, legs: s.legs.filter(l => l.id !== id) }));
  const duplicateLeg = (id: string) => setStrategy(s => s.legs.length >= 12 ? s : ({ ...s, legs: s.legs.flatMap(l => l.id === id ? [l, { ...l, id: uid() }] : [l]) }));
  const moveLeg = (index: number, direction: -1 | 1) => setStrategy(s => { const legs = [...s.legs]; const target = index + direction; if (target < 0 || target >= legs.length) return s; [legs[index], legs[target]] = [legs[target], legs[index]]; return { ...s, legs }; });
  const addLeg = () => { if (strategy.legs.length >= 12) return; const leg = newLeg({ expiry: strategy.legs[0]?.expiry || today() }); setStrategy(s => ({ ...s, legs: [...s.legs, leg] })); setExpanded(leg.id); };

  async function scheduleStrategy() {
    if (!liveEnabled) {
      setError("Start the Docker backend before scheduling a live strategy.");
      return;
    }
    const validation = strategyValidation(strategy);
    if (validation.fields.size) {
      setInvalidFields(validation.fields);
      setError(`Complete the ${validation.fields.size} highlighted ${validation.fields.size === 1 ? "field" : "fields"} before scheduling.`);
      if (validation.firstLegId) setExpanded(validation.firstLegId);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const invalid = document.querySelector<HTMLElement>(".field.invalid, .segmented-field.invalid, .risk-compact-control.invalid, .legs-panel.invalid, .leg-row.has-errors");
        invalid?.scrollIntoView({ behavior: "smooth", block: "center" });
        invalid?.querySelector<HTMLElement>("input, select, button")?.focus();
      }));
      return;
    }
    setExecuting(true); setError(""); setInvalidFields(new Set());
    try {
      const liveStrategy = { ...strategy, acknowledgement: true as const };
      await requestJson<{ result: { id: string } }>("/api/strategies", { method: "POST", body: JSON.stringify({ strategy: liveStrategy, status: "scheduled" }) });
      onNotice({ tone: "ok", text: `Strategy scheduled for ${formatDateTime(strategy.entry.entryAt)}. It will not execute before that time.` });
    } catch (err) { setError(errorMessage(err)); }
    finally { setExecuting(false); }
  }

  function exportDraft() {
    const payload = JSON.stringify({ version: 1, exportedAt: new Date().toISOString(), strategy }, null, 2);
    const href = URL.createObjectURL(new Blob([payload], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${strategy.name.trim().replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase() || "delta-strategy"}.json`;
    anchor.click();
    URL.revokeObjectURL(href);
    onNotice({ tone: "ok", text: "Strategy exported. Import this file from your local trading workspace." });
  }

  async function importDraft(file?: File) {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as unknown;
      const candidate = parsed && typeof parsed === "object" && "strategy" in parsed ? (parsed as { strategy: unknown }).strategy : parsed;
      if (!isStrategyDefinition(candidate)) throw new Error("This file is not a valid Delta strategy draft.");
      setStrategy(hydrateStrategy(candidate)); setExpanded(candidate.legs[0]?.id ?? null); setInvalidFields(new Set()); setError("");
      onNotice({ tone: "ok", text: "Strategy draft imported successfully." });
    } catch (importError) { onNotice({ tone: "error", text: errorMessage(importError) }); }
    finally { if (importInput.current) importInput.current.value = ""; }
  }

  return <div className="builder-page">
    <section className="page-heading" data-reveal><div><div className="eyebrow"><span /> Strategy configuration</div><h1>Strategy builder</h1><p>Configure positions, timing, and risk controls.</p></div><div className="draft-toolbar"><div className="draft-state"><span>Local draft</span><small>Saved on this device</small></div><div><button className="ghost-button" onClick={() => importInput.current?.click()}><Upload />Import</button><button className="secondary-button" onClick={exportDraft}><Download />Export</button><input ref={importInput} className="visually-hidden" type="file" accept="application/json,.json" onChange={event => void importDraft(event.target.files?.[0])} /></div></div></section>
    <div className="strategy-name-row panel" data-reveal><Field label="Strategy name" invalid={invalidFields.has("name")}><input value={strategy.name} maxLength={80} onChange={e => setStrategy({ ...strategy, name: e.target.value })} /></Field><div className="builder-summary"><span><strong>{strategy.legs.length}</strong> legs</span><span><strong>{strategy.legs.reduce((n, l) => n + l.lots, 0)}</strong> total lots</span><span><strong>{strategy.instrument.index}</strong> index</span></div></div>
    <div className="settings-grid">
      <SettingsPanel icon={<CircleDollarSign />} title="Instrument settings" description="Choose the contract family and price source.">
        <div className="field-grid two"><Select label="Index" value={strategy.instrument.index} onChange={v => setStrategy({ ...strategy, instrument: { ...strategy.instrument, index: v as "BTCUSD" | "ETHUSD", underlying: v === "BTCUSD" ? "BTC" : "ETH" } })} options={["BTCUSD", "ETHUSD"]} /><Segmented label="Underlying from" value={strategy.instrument.underlyingFrom} onChange={v => setStrategy({ ...strategy, instrument: { ...strategy.instrument, underlyingFrom: v as "cash" | "futures" } })} options={[{ value: "cash", label: "Cash" }, { value: "futures", label: "Futures" }]} /></div>
      </SettingsPanel>
      <SettingsPanel icon={<Clock3 />} title="Entry settings" description="Set the strategy lifecycle and schedule.">
        <Segmented label="Strategy type" value={strategy.entry.strategyType} onChange={v => setStrategy({ ...strategy, entry: { ...strategy.entry, strategyType: v as "intraday" | "btst" | "positional" } })} options={[{ value: "intraday", label: "Intraday" }, { value: "btst", label: "BTST" }, { value: "positional", label: "Positional" }]} />
        <div className="field-grid two"><Field label="Entry time" invalid={invalidFields.has("entryAt")}><input type="datetime-local" value={toLocal(strategy.entry.entryAt)} onChange={e => setStrategy({ ...strategy, entry: { ...strategy.entry, entryAt: iso(e.target.value) } })} /></Field><Field label="Exit time" invalid={invalidFields.has("exitAt")}><input type="datetime-local" value={toLocal(strategy.entry.exitAt)} onChange={e => setStrategy({ ...strategy, entry: { ...strategy.entry, exitAt: iso(e.target.value) } })} /></Field></div>
      </SettingsPanel>
      <SettingsPanel wide icon={<Shield />} title="Risk control" description="Set how loss protection closes the position.">
        <RiskControl strategy={strategy} onChange={setStrategy} invalidFields={invalidFields} />
      </SettingsPanel>
    </div>
    <section className={`legs-panel panel${invalidFields.has("legs") ? " invalid" : ""}`} data-reveal>
      <div className="panel-title"><div><span className="heading-icon"><Layers3 /></span><div><h2>Legs</h2><p>Call + put</p></div></div><button className="secondary-button" onClick={addLeg} disabled={strategy.legs.length >= 12}><Plus />Add leg <span>{strategy.legs.length}/12</span></button></div>
      <div className="leg-list">
        {strategy.legs.map((leg, index) => <LegRow key={leg.id} leg={leg} riskMode={strategy.riskMode} index={index} total={strategy.legs.length} open={expanded === leg.id} invalidFields={invalidFields} onToggle={() => setExpanded(expanded === leg.id ? null : leg.id)} onUpdate={(patch) => updateLeg(leg.id, patch)} onRemove={() => removeLeg(leg.id)} onDuplicate={() => duplicateLeg(leg.id)} onMove={d => moveLeg(index, d)} />)}
      </div>
      {strategy.legs.length === 0 && <div className="empty-state"><Layers3 /><h3>No strategy legs</h3><p>Add at least one option leg to continue.</p><button className="secondary-button" onClick={addLeg}><Plus />Add first leg</button></div>}
    </section>
    <footer className="builder-actions" data-reveal>
      <div className="live-execution-copy"><ShieldCheck /><p><strong>Scheduled execution</strong><small>Orders are sent only when the configured entry time is reached.</small></p></div>
      <div>{error && <span className="action-error" role="alert"><AlertTriangle />{error}</span>}<button className={liveEnabled ? "primary-button" : "secondary-button"} disabled={executing || !liveEnabled} onClick={scheduleStrategy}>{executing ? <LoaderCircle className="spin" /> : liveEnabled ? <Clock3 /> : <WifiOff />}{executing ? "Scheduling strategy" : liveEnabled ? "Schedule strategy" : "Local backend required"}</button></div>
    </footer>
  </div>;
}

const toLocal = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

function SettingsPanel({ icon, title, description, wide, children }: { icon: React.ReactNode; title: string; description: string; wide?: boolean; children: React.ReactNode }) {
  return <section className={`panel settings-panel${wide ? " wide" : ""}`} data-reveal><div className="panel-title"><div><span className="heading-icon">{icon}</span><div><h2>{title}</h2><p>{description}</p></div></div></div>{children}</section>;
}

function RiskControl({ strategy, onChange, invalidFields }: { strategy: StrategyDefinition; onChange: (strategy: StrategyDefinition) => void; invalidFields: Set<string> }) {
  const combined = strategy.riskMode === "combined_premium";
  const stop = strategy.combinedStopLossPercent ?? 100;
  const exitMultiple = 1 + stop / 100;
  const setMode = (mode: string) => onChange({
    ...strategy,
    riskMode: mode as StrategyDefinition["riskMode"],
    squareOff: mode === "combined_premium" ? "complete" : strategy.squareOff,
    combinedStopLossPercent: mode === "combined_premium" ? stop : strategy.combinedStopLossPercent
  });
  return <div className={`risk-console ${combined ? "combined" : "legwise"}`}>
    <div className="risk-mode-row">
      <Segmented label="Trigger" value={strategy.riskMode} onChange={setMode} invalid={invalidFields.has("riskMode")} options={[{ value: "combined_premium", label: "Combined" }, { value: "legwise", label: "Per leg" }]} />
      <div className="risk-status" aria-label={combined ? "Both short legs close together" : "Each leg closes independently"}><i /><span>{combined ? "Paired exit" : "Independent"}</span></div>
    </div>
    {combined ? <div className="risk-compact-grid">
      <label className={`risk-compact-control risk-stop-compact${invalidFields.has("combinedStopLossPercent") ? " invalid" : ""}`}>
        <span>Combined stop loss</span>
        <div><input aria-label="Combined stop loss percent" type="number" min="1" max="1000" value={stop} onChange={event => onChange({ ...strategy, combinedStopLossPercent: Math.max(1, Number(event.target.value) || 1) })} /><b>%</b></div>
        <small>Closes both legs at this total premium loss.</small>
      </label>
      <div className="risk-compact-stat entry"><span>Entry premium</span><strong>1×</strong><small>Reference</small></div>
      <div className="risk-compact-stat exit"><span>Exit trigger</span><strong>{Number.isInteger(exitMultiple) ? exitMultiple.toFixed(0) : exitMultiple.toFixed(1)}×</strong><small>Combined premium</small></div>
      <label className="risk-compact-control risk-backup-compact">
        <span><ShieldCheck />Emergency stop / leg</span>
        <div><input aria-label="Emergency stop loss percent per leg" type="number" min="1" max="5000" value={strategy.emergencyStopLossPercent ?? ""} onChange={event => onChange({ ...strategy, emergencyStopLossPercent: event.target.value ? Number(event.target.value) : undefined })} placeholder="Off" /><b>%</b></div>
        <small>Optional individual hard limit.</small>
      </label>
    </div> : <div className="legwise-row compact"><Segmented label="Square off" value={strategy.squareOff} onChange={value => onChange({ ...strategy, squareOff: value as "partial" | "complete" })} options={[{ value: "partial", label: "Partial" }, { value: "complete", label: "Complete" }]} /><Toggle checked={strategy.trailToBreakEven} onChange={value => onChange({ ...strategy, trailToBreakEven: value })} label="Trail to break-even" /><div className="legwise-cue"><Shield /><small>Stops stay inside each leg</small></div></div>}
  </div>;
}

function LegRow({ leg, riskMode, index, total, open, invalidFields, onToggle, onUpdate, onRemove, onDuplicate, onMove }: { leg: StrategyLeg; riskMode: StrategyDefinition["riskMode"]; index: number; total: number; open: boolean; invalidFields: Set<string>; onToggle: () => void; onUpdate: (p: Partial<StrategyLeg>) => void; onRemove: () => void; onDuplicate: () => void; onMove: (d: -1 | 1) => void }) {
  const invalid = (field: string) => invalidFields.has(`leg.${leg.id}.${field}`);
  const hasErrors = Array.from(invalidFields).some(field => field.startsWith(`leg.${leg.id}.`));
  return <article className={`${open ? "leg-row open t-acc" : "leg-row t-acc"}${hasErrors ? " has-errors" : ""}`} data-open={open}>
    <div className="leg-summary">
      <button className="drag-handle" aria-label={`Reorder leg ${index + 1}`}><GripVertical /></button>
      <button className="leg-toggle" onClick={onToggle} aria-expanded={open}><span className="leg-number">{String(index + 1).padStart(2, "0")}</span><span className={`side ${leg.position}`}>{leg.position}</span><strong>{leg.optionType === "call" ? "Call" : "Put"}</strong><span>{leg.lots} {leg.lots === 1 ? "lot" : "lots"}</span><span>{leg.strikeMode.toUpperCase()}{leg.strikeMode !== "exact" && leg.strikeSteps ? ` ${leg.strikeSteps}` : ""}</span><span>{formatDate(leg.expiry)}</span><span className="t-acc-chevron"><ChevronDown /></span></button>
      <div className="leg-tools"><button onClick={() => onMove(-1)} disabled={index === 0} aria-label="Move leg up"><ArrowUp /></button><button onClick={() => onMove(1)} disabled={index === total - 1} aria-label="Move leg down"><ArrowDown /></button><button onClick={onDuplicate} disabled={total >= 12} aria-label="Duplicate leg"><Copy /></button><button onClick={onRemove} disabled={total === 1} aria-label="Delete leg" className="danger"><Trash2 /></button></div>
    </div>
    <div className="t-acc-panel" aria-hidden={!open}><div className="t-acc-panel-inner" inert={!open ? true : undefined}><div className="leg-body">
      <div className="leg-grid primary-fields"><NumberField label="Lots" min={1} value={leg.lots} invalid={invalid("lots")} onChange={v => onUpdate({ lots: v })} /><Segmented label="Position" value={leg.position} onChange={v => onUpdate({ position: v as "buy" | "sell" })} options={[{ value: "buy", label: "Buy" }, { value: "sell", label: "Sell" }]} /><Segmented label="Option type" value={leg.optionType} onChange={v => onUpdate({ optionType: v as "call" | "put" })} options={[{ value: "call", label: "Call" }, { value: "put", label: "Put" }]} /><Field label="Expiry" invalid={invalid("expiry")}><input type="date" min={new Date().toISOString().slice(0,10)} value={leg.expiry} onChange={e => onUpdate({ expiry: e.target.value })} /></Field><Select label="Strike criteria" value={leg.strikeMode} onChange={v => onUpdate({ strikeMode: v as StrategyLeg["strikeMode"] })} options={["atm", "itm", "otm", "exact"]} />{leg.strikeMode === "exact" ? <OptionalNumber label="Exact strike" value={leg.exactStrike} invalid={invalid("exactStrike")} onChange={v => onUpdate({ exactStrike: v })} /> : <NumberField label="Strike steps" min={0} max={100} value={leg.strikeSteps} onChange={v => onUpdate({ strikeSteps: v })} />}</div>
      <div className="subsection-label">Order & protection</div>
      <div className={`leg-grid risk-fields${riskMode === "combined_premium" ? " combined" : ""}`}><Segmented label="Order type" value={leg.orderType} onChange={v => onUpdate({ orderType: v as "market_order" | "limit_order" })} options={[{ value: "market_order", label: "Market" }, { value: "limit_order", label: "Limit" }]} />{leg.orderType === "limit_order" && <Field label="Limit price" invalid={invalid("limitPrice")}><input inputMode="decimal" value={leg.limitPrice || ""} onChange={e => onUpdate({ limitPrice: e.target.value || undefined })} placeholder="0.00" /></Field>}{riskMode === "legwise" && <><OptionalNumber label="Target profit" value={leg.targetProfit} onChange={v => onUpdate({ targetProfit: v })} /><OptionalNumber label={`Stop loss${leg.position === "sell" ? " (required)" : ""}`} value={leg.stopLoss} invalid={invalid("stopLoss")} onChange={v => onUpdate({ stopLoss: v })} /><OptionalNumber label="Trail SL" value={leg.trailStop} onChange={v => onUpdate({ trailStop: v })} /><NumberField label="Re-entry on target" min={0} max={10} value={leg.reentryOnTarget} onChange={v => onUpdate({ reentryOnTarget: v })} /><NumberField label="Re-entry on SL" min={0} max={10} value={leg.reentryOnStop} onChange={v => onUpdate({ reentryOnStop: v })} /></>}</div>
      {riskMode === "combined_premium" && <div className="combined-leg-cue"><Shield /><span />Protected by combined trigger</div>}
    </div></div></div>
  </article>;
}

function Dashboard({ onNotice }: { onNotice: (n: { tone: "ok" | "error"; text: string }) => void }) {
  const [data, setData] = useState<Overview | null>(null); const [loading, setLoading] = useState(true); const [cancel, setCancel] = useState<{ id: string; productId: number; symbol: string } | null>(null); const [close, setClose] = useState<{ productId: number; symbol: string; size: string } | null>(null);
  const load = useCallback(async () => { setLoading(true); try { setData(await requestJson<Overview>("/api/account/overview")); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } finally { setLoading(false); } }, [onNotice]);
  useEffect(() => { void load(); }, [load]);
  const balance = useMemo(() => totalBalance(data?.balances || []), [data]);
  async function cancelOrder() { if (!cancel) return; try { await requestJson(`/api/orders/${cancel.id}`, { method: "DELETE", body: JSON.stringify({ productId: cancel.productId, confirm: true }) }); onNotice({ tone: "ok", text: `Order ${cancel.id} cancelled.` }); setCancel(null); void load(); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } }
  async function closePosition() { if (!close) return; try { await requestJson(`/api/positions/${close.productId}/close`, { method: "POST", body: JSON.stringify({ confirm: true }) }); onNotice({ tone: "ok", text: `${close.symbol} position closed and verified on Delta.` }); setClose(null); void load(); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } }
  return <div className="dashboard-page"><section className="page-heading" data-reveal><div><div className="eyebrow"><span /> Account overview</div><h1>Trading dashboard</h1><p>Live balances, positions, and outstanding Delta orders.</p></div><button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} />Refresh</button></section>
    {loading ? <PanelSkeleton /> : <><div className="metric-grid"><Metric icon={<Wallet />} label="Estimated balance" value={balance} note="Across returned assets" /><Metric icon={<TrendingUp />} label="Open positions" value={String(data?.positions.length || 0)} note="Live Delta positions" /><Metric icon={<Clock3 />} label="Open orders" value={String(data?.orders.length || 0)} note="Awaiting fill or cancellation" /></div>{Boolean(data?.riskStrategies.length) && <LiveRisk strategies={data?.riskStrategies || []} />}<DataPanel title="Open positions" icon={<BarChart3 />} rows={data?.positions || []} empty="No open positions" actionLabel="Close" action={(row) => { const productId = Number(row.product_id); if (productId) setClose({ productId, symbol: String(row.product_symbol ?? row.symbol ?? productId), size: String(row.size ?? "") }); }} /><DataPanel title="Open orders" icon={<Clock3 />} rows={data?.orders || []} empty="No open orders" actionLabel="Cancel" action={(row) => { const id = String(row.id ?? row.order_id ?? ""); const productId = Number(row.product_id); if (id && productId) setCancel({ id, productId, symbol: String(row.product_symbol ?? row.symbol ?? id) }); }} /></>}
    {cancel && <ConfirmModal title="Cancel open order?" description={`Order ${cancel.symbol} will be cancelled on Delta Exchange. Filled quantities cannot be reversed.`} confirm="Cancel order" onClose={() => setCancel(null)} onConfirm={() => void cancelOrder()} />}
    {close && <ConfirmModal title="Close entire position?" description={`${close.symbol} size ${close.size} will be closed with a reduce-only market order. All open orders for this contract will be cancelled first, and the live position will be verified afterward.`} confirm="Close position" onClose={() => setClose(null)} onConfirm={() => void closePosition()} />}
  </div>;
}

function RunHistory({ onNotice }: { onNotice: (n: { tone: "ok" | "error"; text: string }) => void }) {
  const [rows, setRows] = useState<StrategyRow[]>([]); const [loading, setLoading] = useState(true); const [action, setAction] = useState<{ row: StrategyRow; kind: "cancel" | "exit" } | null>(null);
  const load = useCallback(async () => { setLoading(true); try { const data = await requestJson<{ result: StrategyRow[] }>("/api/strategies"); setRows(data.result); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } finally { setLoading(false); } }, [onNotice]);
  useEffect(() => { void load(); }, [load]);
  async function runAction() { if (!action) return; const { row, kind } = action; try { if (kind === "cancel") { await requestJson(`/api/strategies/${row.id}`, { method: "DELETE" }); onNotice({ tone: "ok", text: `${row.name} cancelled before entry.` }); } else { await requestJson(`/api/strategies/${row.id}/exit`, { method: "POST", body: JSON.stringify({ confirm: true }) }); onNotice({ tone: "ok", text: `${row.name} exited and verified on Delta.` }); } setAction(null); void load(); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } }
  return <div><section className="page-heading" data-reveal><div><div className="eyebrow"><span /> Strategy operations</div><h1>Run history</h1><p>Review scheduled, active, completed, and attention-required strategies.</p></div><button className="secondary-button" onClick={() => void load()}><RefreshCw className={loading ? "spin" : ""} />Refresh</button></section><section className="panel run-panel" data-reveal>{loading ? <PanelSkeleton /> : rows.length ? <div className="run-list">{rows.map(row => { const canCancel = ["draft", "scheduled"].includes(row.status); const canExit = row.status === "active" || (row.status === "attention" && Boolean(row.entryExecutedAt)); return <article className="run-row" key={row.id}><span className={`status-dot ${row.status}`} /><div><strong>{row.name}</strong><span>Created {formatDateTime(row.createdAt)}</span></div><span className={`status-chip ${row.status}`}>{row.status.replaceAll("_", " ")}</span><div><small>Entry</small><strong>{formatDateTime(row.entryAt)}</strong></div><div><small>Exit</small><strong>{formatDateTime(row.exitAt)}</strong></div>{row.lastError && <span className="row-error" title={row.lastError}><AlertTriangle />Needs attention</span>}<button className="icon-button danger" disabled={!canCancel && !canExit} onClick={() => setAction({ row, kind: canCancel ? "cancel" : "exit" })} aria-label={`${canCancel ? "Cancel" : "Exit"} ${row.name}`}>{canExit ? <CircleStop /> : <Trash2 />}</button></article>; })}</div> : <div className="empty-state"><Activity /><h3>No strategy runs yet</h3><p>Saved and scheduled strategies will appear here.</p></div>}</section>{action && <ConfirmModal title={action.kind === "cancel" ? "Cancel scheduled strategy?" : "Exit live strategy?"} description={action.kind === "cancel" ? `${action.row.name} will be cancelled before entry and no orders will be placed.` : `${action.row.name} will cancel its still-open entry orders, submit reduce-only market closes for recorded fills, and verify the live Delta positions.`} confirm={action.kind === "cancel" ? "Cancel strategy" : "Exit strategy"} onClose={() => setAction(null)} onConfirm={() => void runAction()} />}</div>;
}

function Metric({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) { return <div className="metric panel" data-reveal><span>{icon}</span><div><small>{label}</small><strong>{value}</strong><p>{note}</p></div></div>; }
function LiveRisk({ strategies }: { strategies: RiskStrategy[] }) {
  return <section className="panel live-risk" data-reveal><div className="panel-title"><div><span className="heading-icon"><Shield /></span><div><h2>Combined protection</h2><p>{strategies.length} {strategies.length === 1 ? "strategy" : "strategies"}</p></div></div><span className="risk-live"><i />Live</span></div><div className="live-risk-list">{strategies.map(strategy => {
    const state = strategy.riskState;
    const stop = Number(state.stopPercent || 100);
    const lossPercent = Math.max(0, Number(state.progress || 0));
    const progress = Math.min(100, stop > 0 ? lossPercent / stop * 100 : 0);
    const status = String(state.status || strategy.status).replaceAll("_", " ");
    return <article className="live-risk-row" key={strategy.id}><div><strong>{strategy.name}</strong><span>{status}</span></div><div className="risk-meter" role="meter" aria-label={`${strategy.name} combined stop usage`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}><i style={{ width: `${progress}%` }} /><b style={{ left: `${progress}%` }} /></div><div className="risk-readout"><strong>{Math.round(lossPercent)}%</strong><span>/ {stop}%</span></div><small>{strategy.monitoredAt ? formatClock(strategy.monitoredAt) : "—"}</small></article>;
  })}</div></section>;
}
function PanelSkeleton() { return <div className="panel skeleton-wrap"><div className="skeleton wide" /><div className="skeleton" /><div className="skeleton" /><div className="skeleton short" /></div>; }

function DataPanel({ title, icon, rows, empty, action, actionLabel = "Action" }: { title: string; icon: React.ReactNode; rows: Record<string, unknown>[]; empty: string; action?: (row: Record<string, unknown>) => void; actionLabel?: string }) {
  const columns = rows.length ? Object.keys(rows[0]).filter(k => ["symbol","product_symbol","side","size","entry_price","mark_price","limit_price","state","unrealized_pnl","id"].includes(k)).slice(0, 6) : [];
  return <section className="panel data-panel" data-reveal><div className="panel-title"><div><span className="heading-icon">{icon}</span><div><h2>{title}</h2><p>{rows.length} live {rows.length === 1 ? "record" : "records"}</p></div></div></div>{rows.length ? <div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{c.replaceAll("_", " ")}</th>)}{action && <th>Action</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? index)}>{columns.map(c => <td key={c}>{formatCell(row[c])}</td>)}{action && <td><button className="text-danger" onClick={() => action(row)}>{actionLabel}</button></td>}</tr>)}</tbody></table></div> : <div className="empty-state compact"><Activity /><h3>{empty}</h3><p>Delta will report new activity here.</p></div>}</section>;
}

function ConfirmModal({ title, description, confirm, onClose, onConfirm }: { title: string; description: string; confirm: string; onClose: () => void; onConfirm: () => void }) { return <div className="modal-layer" role="dialog" aria-modal="true"><button className="drawer-backdrop" onClick={onClose} aria-label="Close confirmation" /><div className="confirm-modal"><span className="danger-icon"><AlertTriangle /></span><h2>{title}</h2><p>{description}</p><div><button className="ghost-button" onClick={onClose}>Keep it</button><button className="danger-button" onClick={onConfirm}>{confirm}</button></div></div></div>; }

function Field({ label, children, invalid = false }: { label: string; children: React.ReactNode; invalid?: boolean }) { return <label className={`field${invalid ? " invalid" : ""}`} aria-invalid={invalid || undefined}><span>{label}</span>{children}</label>; }
function Select({ label, value, options, onChange, invalid = false }: { label: string; value: string; options: string[]; onChange: (v: string) => void; invalid?: boolean }) { return <Field label={label} invalid={invalid}><span className="select-wrap"><select value={value} onChange={e => onChange(e.target.value)}>{options.map(o => <option value={o} key={o}>{o.toUpperCase()}</option>)}</select><ChevronDown /></span></Field>; }
function NumberField({ label, value, min = 0, max, onChange, invalid = false }: { label: string; value: number; min?: number; max?: number; onChange: (v: number) => void; invalid?: boolean }) { return <Field label={label} invalid={invalid}><input type="number" value={value} min={min} max={max} onChange={e => onChange(Number(e.target.value))} /></Field>; }
function OptionalNumber({ label, value, onChange, invalid = false }: { label: string; value?: number; onChange: (v?: number) => void; invalid?: boolean }) { return <Field label={label} invalid={invalid}><input type="number" min="0" step="any" value={value ?? ""} onChange={e => onChange(e.target.value === "" ? undefined : Number(e.target.value))} placeholder="Disabled" /></Field>; }
function Segmented({ label, value, options, onChange, disabled, invalid = false }: { label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; disabled?: boolean; invalid?: boolean }) {
  return <fieldset className={`segmented-field${invalid ? " invalid" : ""}`} aria-invalid={invalid || undefined} disabled={disabled}><legend>{label}</legend><div className="t-tabs" role="tablist" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>{options.map(option => <button type="button" role="tab" aria-selected={value === option.value} className="t-tab" key={option.value} onClick={() => onChange(option.value)}>{option.label}</button>)}</div></fieldset>;
}
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) { return <label className="toggle-field"><span>{label}</span><button type="button" role="switch" aria-checked={checked} className={checked ? "toggle on" : "toggle"} onClick={() => onChange(!checked)}><i /></button></label>; }

function formatDate(value: string) { const date = new Date(`${value}T00:00:00`); return Number.isNaN(date.getTime()) ? "No expiry" : new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "2-digit" }).format(date); }
function formatDateTime(value: string) { const date = new Date(value); if (!value || Number.isNaN(date.getTime())) return "—"; return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(date); }
function formatClock(value: string) { return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)); }
function formatCell(value: unknown) { if (value == null) return "—"; if (typeof value === "object") return JSON.stringify(value); return String(value); }
function totalBalance(rows: unknown[]) { let total = 0; for (const item of rows) { if (item && typeof item === "object") { const r = item as Record<string, unknown>; const value = Number(r.balance ?? r.available_balance ?? r.wallet_balance ?? 0); if (Number.isFinite(value)) total += value; } } return total ? `$${total.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"; }
