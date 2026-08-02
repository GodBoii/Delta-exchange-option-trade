"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import {
  Activity, AlertTriangle, ArrowDown, ArrowUp, BarChart3, Bell, Check, ChevronDown,
  CircleDollarSign, Clock3, Copy, ExternalLink, GripVertical, KeyRound, Layers3,
  LayoutDashboard, LoaderCircle, LockKeyhole, LogOut, Menu, Plus, RefreshCw,
  Save, ShieldCheck, SlidersHorizontal, Trash2, TrendingUp, Wallet,
  X, Zap
} from "lucide-react";
import type { StrategyDefinition, StrategyLeg } from "@/lib/strategy";

type Account = { id: string; accountName?: string | null; email?: string | null; environment: "production" | "testnet" };
type SessionResponse = { success: boolean; connected: boolean; account: Account | null; message?: string; error?: string };
type StrategyRow = { id: string; name: string; status: string; entryAt: string; exitAt: string; lastError?: string | null; createdAt: string };
type ResolvedLeg = StrategyLeg & { productId: number; productSymbol: string; strike: number; markPrice: string | null };
type PreviewData = { definition: StrategyDefinition; legs: ResolvedLeg[]; warnings: string[] };
type Overview = { balances: unknown[]; orders: Record<string, unknown>[]; positions: Record<string, unknown>[] };
type Tab = "builder" | "dashboard" | "runs";

const today = () => new Date(Date.now() + 86400000).toISOString().slice(0, 10);
const localDateTime = (offsetHours: number) => {
  const date = new Date(Date.now() + offsetHours * 3600000);
  date.setMinutes(Math.ceil(date.getMinutes() / 5) * 5, 0, 0);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const iso = (value: string) => new Date(value).toISOString();
const uid = () => globalThis.crypto?.randomUUID?.().slice(0, 12) ?? Math.random().toString(36).slice(2, 14);

const newLeg = (overrides: Partial<StrategyLeg> = {}): StrategyLeg => ({
  id: uid(), lots: 1, position: "buy", optionType: "call", expiry: today(), strikeMode: "atm",
  strikeSteps: 0, orderType: "market_order", reentryOnTarget: 0, reentryOnStop: 0, ...overrides
});

const initialStrategy = (): StrategyDefinition => ({
  name: "BTC intraday spread",
  instrument: { index: "BTCUSD", underlying: "BTC", underlyingFrom: "cash" },
  entry: { strategyType: "intraday", entryAt: iso(localDateTime(1)), exitAt: iso(localDateTime(8)) },
  squareOff: "complete", trailToBreakEven: false, breakEvenScope: "all_legs",
  legs: [newLeg(), newLeg({ position: "sell", strikeMode: "otm", strikeSteps: 2, stopLoss: 25 })],
  acknowledgement: false as true
});

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
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
  const [account, setAccount] = useState<Account | null>(null);
  const [tab, setTab] = useState<Tab>("builder");
  const [mobileNav, setMobileNav] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  useGSAP(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.from("[data-reveal]", { opacity: 0, y: 14, duration: .55, stagger: .06, ease: "power3.out" });
  }, { scope: root, dependencies: [account?.id, tab] });

  const loadSession = useCallback(async () => {
    try {
      const data = await requestJson<SessionResponse>("/api/session");
      setAccount(data.connected ? data.account : null);
    } catch (error) { setNotice({ tone: "error", text: errorMessage(error) }); }
    finally { setSessionLoading(false); }
  }, []);

  useEffect(() => { void loadSession(); }, [loadSession]);

  async function disconnect() {
    try {
      await requestJson("/api/session", { method: "DELETE" });
      setAccount(null); setTab("builder"); setNotice({ tone: "ok", text: "Delta account disconnected on this device." });
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
              <NavButton active={tab === "dashboard"} icon={<LayoutDashboard />} onClick={() => { setTab("dashboard"); setMobileNav(false); }}>Dashboard</NavButton>
              <NavButton active={tab === "runs"} icon={<Activity />} onClick={() => { setTab("runs"); setMobileNav(false); }}>Run history</NavButton>
            </nav>
            <div className="account-cluster">
              <span className={`environment ${account.environment}`}><i />{account.environment}</span>
              <button className="icon-button notification" aria-label="Notifications"><Bell /><span /></button>
              <div className="account-copy"><strong>{account.accountName || "Delta account"}</strong><span>{account.email || "Connected securely"}</span></div>
              <button className="icon-button" onClick={disconnect} aria-label="Disconnect Delta account" title="Disconnect"><LogOut /></button>
              <button className="icon-button nav-toggle" onClick={() => setMobileNav(v => !v)} aria-label="Toggle navigation"><Menu /></button>
            </div>
          </header>
          <main className="workspace">
            {notice && <Toast tone={notice.tone} onClose={() => setNotice(null)}>{notice.text}</Toast>}
            {tab === "builder" && <StrategyBuilder onNotice={setNotice} />}
            {tab === "dashboard" && <Dashboard onNotice={setNotice} />}
            {tab === "runs" && <RunHistory onNotice={setNotice} />}
          </main>
        </>
      ) : <ConnectView onConnected={(next) => { setAccount(next); setNotice({ tone: "ok", text: "Connection verified. Your encrypted session is ready." }); }} />}
    </div>
  );
}

function Brand() {
  return <div className="brand" aria-label="Delta Strategy Desk"><span className="brand-mark"><i /><i /><i /></span><span><strong>Delta</strong><small>Strategy Desk</small></span></div>;
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

function ConnectView({ onConnected }: { onConnected: (account: Account) => void }) {
  const panel = useRef<HTMLDivElement>(null);
  const [apiKey, setApiKey] = useState(""); const [apiSecret, setApiSecret] = useState("");
  const [environment, setEnvironment] = useState<"production" | "testnet">("testnet");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useGSAP(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.from(".connect-copy > *, .connect-card", { opacity: 0, y: 24, stagger: .08, duration: .7, ease: "power3.out" });
  }, { scope: panel });

  async function connect(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await requestJson<{ account: Account }>("/api/session/connect", { method: "POST", body: JSON.stringify({ apiKey, apiSecret, environment }) });
      setApiKey(""); setApiSecret(""); onConnected(result.account);
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  return <main className="connect-shell" ref={panel}>
    <header className="connect-header"><Brand /><a href="https://docs.delta.exchange/" target="_blank" rel="noreferrer">Delta API docs <ExternalLink /></a></header>
    <div className="connect-grid">
      <section className="connect-copy">
        <div className="eyebrow"><span /> Strategy operations console</div>
        <h1>Build option strategies with execution-grade clarity.</h1>
        <p>Connect your Delta Exchange account to resolve live contracts, schedule entries, and monitor every strategy from one controlled workspace.</p>
        <div className="trust-row"><div><ShieldCheck /><span><strong>Encrypted at rest</strong><small>Your API secret never returns to the browser.</small></span></div><div><LockKeyhole /><span><strong>Persistent secure session</strong><small>Reconnect only when you choose to sign out.</small></span></div></div>
      </section>
      <form className="connect-card" onSubmit={connect} aria-label="Connect Delta Exchange account">
        <div className="card-heading"><span className="heading-icon"><KeyRound /></span><div><h2>Connect Delta Exchange</h2><p>Verify trading access to continue.</p></div></div>
        <Segmented label="Environment" value={environment} onChange={(v) => setEnvironment(v as "production" | "testnet")} options={[{ value: "production", label: "Production" }, { value: "testnet", label: "Testnet" }]} />
        {environment === "production" && <div className="production-warning"><AlertTriangle /><p><strong>Production uses real funds.</strong><br />Orders confirmed in this environment are sent to your live Delta account.</p></div>}
        <Field label="API key"><input value={apiKey} onChange={e => setApiKey(e.target.value)} autoComplete="off" spellCheck={false} placeholder="Paste your API key" minLength={16} required /></Field>
        <Field label="API secret"><input type="password" value={apiSecret} onChange={e => setApiSecret(e.target.value)} autoComplete="new-password" placeholder="Paste your API secret" minLength={24} required /></Field>
        {error && <div className="inline-error" role="alert"><AlertTriangle />{error}</div>}
        <div className="security-note"><ShieldCheck /><p><strong>Trading access is verified before connection.</strong><br />Use a dedicated key with only the permissions this workstation needs.</p></div>
        <button className="primary-button full" disabled={busy}>{busy ? <><LoaderCircle className="spin" />Verifying connection</> : <><Zap />Connect securely</>}</button>
        <p className="terms">Connecting does not place an order. Every execution requires a reviewed preview and explicit confirmation.</p>
      </form>
    </div>
  </main>;
}

function StrategyBuilder({ onNotice }: { onNotice: (n: { tone: "ok" | "error"; text: string }) => void }) {
  const [strategy, setStrategy] = useState<StrategyDefinition>(initialStrategy);
  const [expanded, setExpanded] = useState<string | null>(strategy.legs[0].id);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");

  const updateLeg = (id: string, patch: Partial<StrategyLeg>) => setStrategy(s => ({ ...s, legs: s.legs.map(l => l.id === id ? { ...l, ...patch } : l) }));
  const removeLeg = (id: string) => setStrategy(s => ({ ...s, legs: s.legs.filter(l => l.id !== id) }));
  const duplicateLeg = (id: string) => setStrategy(s => s.legs.length >= 12 ? s : ({ ...s, legs: s.legs.flatMap(l => l.id === id ? [l, { ...l, id: uid() }] : [l]) }));
  const moveLeg = (index: number, direction: -1 | 1) => setStrategy(s => { const legs = [...s.legs]; const target = index + direction; if (target < 0 || target >= legs.length) return s; [legs[index], legs[target]] = [legs[target], legs[index]]; return { ...s, legs }; });
  const addLeg = () => { if (strategy.legs.length >= 12) return; const leg = newLeg({ expiry: strategy.legs[0]?.expiry || today() }); setStrategy(s => ({ ...s, legs: [...s.legs, leg] })); setExpanded(leg.id); };

  async function openPreview() {
    setPreviewing(true); setError("");
    try {
      const data = await requestJson<{ success: true } & PreviewData>("/api/strategies/preview", { method: "POST", body: JSON.stringify(strategy) });
      setPreview(data);
    } catch (err) { setError(errorMessage(err)); }
    finally { setPreviewing(false); }
  }

  return <div className="builder-page">
    <section className="page-heading" data-reveal><div><div className="eyebrow"><span /> Strategy workspace</div><h1>Build a new strategy</h1><p>Configure, resolve, and verify every leg before it reaches Delta.</p></div><div className="draft-state"><span>Unsaved strategy</span><small>Changes remain on this device</small></div></section>
    <div className="strategy-name-row panel" data-reveal><Field label="Strategy name"><input value={strategy.name} maxLength={80} onChange={e => setStrategy({ ...strategy, name: e.target.value })} /></Field><div className="builder-summary"><span><strong>{strategy.legs.length}</strong> legs</span><span><strong>{strategy.legs.reduce((n, l) => n + l.lots, 0)}</strong> total lots</span><span><strong>{strategy.instrument.index}</strong> index</span></div></div>
    <div className="settings-grid">
      <SettingsPanel icon={<CircleDollarSign />} title="Instrument settings" description="Choose the contract family and price source.">
        <div className="field-grid two"><Select label="Index" value={strategy.instrument.index} onChange={v => setStrategy({ ...strategy, instrument: { ...strategy.instrument, index: v as "BTCUSD" | "ETHUSD", underlying: v === "BTCUSD" ? "BTC" : "ETH" } })} options={["BTCUSD", "ETHUSD"]} /><Segmented label="Underlying from" value={strategy.instrument.underlyingFrom} onChange={v => setStrategy({ ...strategy, instrument: { ...strategy.instrument, underlyingFrom: v as "cash" | "futures" } })} options={[{ value: "cash", label: "Cash" }, { value: "futures", label: "Futures" }]} /></div>
      </SettingsPanel>
      <SettingsPanel icon={<Clock3 />} title="Entry settings" description="Set the strategy lifecycle and schedule.">
        <Segmented label="Strategy type" value={strategy.entry.strategyType} onChange={v => setStrategy({ ...strategy, entry: { ...strategy.entry, strategyType: v as "intraday" | "btst" | "positional" } })} options={[{ value: "intraday", label: "Intraday" }, { value: "btst", label: "BTST" }, { value: "positional", label: "Positional" }]} />
        <div className="field-grid two"><Field label="Entry time"><input type="datetime-local" value={toLocal(strategy.entry.entryAt)} onChange={e => setStrategy({ ...strategy, entry: { ...strategy.entry, entryAt: iso(e.target.value) } })} /></Field><Field label="Exit time"><input type="datetime-local" value={toLocal(strategy.entry.exitAt)} onChange={e => setStrategy({ ...strategy, entry: { ...strategy.entry, exitAt: iso(e.target.value) } })} /></Field></div>
      </SettingsPanel>
      <SettingsPanel wide icon={<SlidersHorizontal />} title="Legwise settings" description="Define how protection and square-off rules apply across legs.">
        <div className="legwise-row"><Segmented label="Square off" value={strategy.squareOff} onChange={v => setStrategy({ ...strategy, squareOff: v as "partial" | "complete" })} options={[{ value: "partial", label: "Partial" }, { value: "complete", label: "Complete" }]} /><Toggle checked={strategy.trailToBreakEven} onChange={v => setStrategy({ ...strategy, trailToBreakEven: v })} label="Trail SL to break-even" /><Segmented disabled={!strategy.trailToBreakEven} label="Break-even scope" value={strategy.breakEvenScope} onChange={v => setStrategy({ ...strategy, breakEvenScope: v as "all_legs" | "stop_loss_legs" })} options={[{ value: "all_legs", label: "All legs" }, { value: "stop_loss_legs", label: "SL legs" }]} /><OptionalNumber label="Overall target" value={strategy.overallTarget} onChange={v => setStrategy({ ...strategy, overallTarget: v })} /><OptionalNumber label="Overall stop loss" value={strategy.overallStopLoss} onChange={v => setStrategy({ ...strategy, overallStopLoss: v })} /></div>
        <p className="worker-note"><Activity /> Cross-leg target, stop, and break-even values are saved for review; automated monitoring is not enabled in this worker version.</p>
      </SettingsPanel>
    </div>
    <section className="legs-panel panel" data-reveal>
      <div className="panel-title"><div><span className="heading-icon"><Layers3 /></span><div><h2>Leg builder</h2><p>Combine up to 12 options contracts. Short legs require a stop loss.</p></div></div><button className="secondary-button" onClick={addLeg} disabled={strategy.legs.length >= 12}><Plus />Add leg <span>{strategy.legs.length}/12</span></button></div>
      <div className="leg-list">
        {strategy.legs.map((leg, index) => <LegRow key={leg.id} leg={leg} index={index} total={strategy.legs.length} open={expanded === leg.id} onToggle={() => setExpanded(expanded === leg.id ? null : leg.id)} onUpdate={(patch) => updateLeg(leg.id, patch)} onRemove={() => removeLeg(leg.id)} onDuplicate={() => duplicateLeg(leg.id)} onMove={d => moveLeg(index, d)} />)}
      </div>
      {strategy.legs.length === 0 && <div className="empty-state"><Layers3 /><h3>No strategy legs</h3><p>Add at least one option leg to continue.</p><button className="secondary-button" onClick={addLeg}><Plus />Add first leg</button></div>}
    </section>
    <footer className="builder-actions" data-reveal>
      <label className="acknowledgement"><input type="checkbox" checked={strategy.acknowledgement} onChange={e => setStrategy({ ...strategy, acknowledgement: e.target.checked as true })} /><span><Check /></span><p><strong>I understand the execution risk.</strong><small>Legs are submitted sequentially and market prices can change after preview.</small></p></label>
      <div>{error && <span className="action-error"><AlertTriangle />{error}</span>}<button className="primary-button" disabled={previewing || !strategy.acknowledgement || strategy.legs.length === 0} onClick={openPreview}>{previewing ? <LoaderCircle className="spin" /> : <Zap />}{previewing ? "Resolving contracts" : "Preview strategy"}</button></div>
    </footer>
    {preview && <PreviewDrawer preview={preview} strategy={strategy} onClose={() => setPreview(null)} onNotice={onNotice} />}
  </div>;
}

const toLocal = (value: string) => new Date(new Date(value).getTime() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);

function SettingsPanel({ icon, title, description, wide, children }: { icon: React.ReactNode; title: string; description: string; wide?: boolean; children: React.ReactNode }) {
  return <section className={`panel settings-panel${wide ? " wide" : ""}`} data-reveal><div className="panel-title"><div><span className="heading-icon">{icon}</span><div><h2>{title}</h2><p>{description}</p></div></div></div>{children}</section>;
}

function LegRow({ leg, index, total, open, onToggle, onUpdate, onRemove, onDuplicate, onMove }: { leg: StrategyLeg; index: number; total: number; open: boolean; onToggle: () => void; onUpdate: (p: Partial<StrategyLeg>) => void; onRemove: () => void; onDuplicate: () => void; onMove: (d: -1 | 1) => void }) {
  const body = useRef<HTMLDivElement>(null);
  useGSAP(() => { if (!body.current || matchMedia("(prefers-reduced-motion: reduce)").matches) return; gsap.fromTo(body.current, { height: 0, opacity: 0 }, { height: "auto", opacity: 1, duration: .36, ease: "power2.out" }); }, { scope: body, dependencies: [open] });
  return <article className={open ? "leg-row open" : "leg-row"}>
    <div className="leg-summary">
      <button className="drag-handle" aria-label={`Reorder leg ${index + 1}`}><GripVertical /></button>
      <button className="leg-toggle" onClick={onToggle} aria-expanded={open}><span className="leg-number">{String(index + 1).padStart(2, "0")}</span><span className={`side ${leg.position}`}>{leg.position}</span><strong>{leg.optionType === "call" ? "Call" : "Put"}</strong><span>{leg.lots} {leg.lots === 1 ? "lot" : "lots"}</span><span>{leg.strikeMode.toUpperCase()}{leg.strikeMode !== "exact" && leg.strikeSteps ? ` ${leg.strikeSteps}` : ""}</span><span>{formatDate(leg.expiry)}</span><ChevronDown /></button>
      <div className="leg-tools"><button onClick={() => onMove(-1)} disabled={index === 0} aria-label="Move leg up"><ArrowUp /></button><button onClick={() => onMove(1)} disabled={index === total - 1} aria-label="Move leg down"><ArrowDown /></button><button onClick={onDuplicate} disabled={total >= 12} aria-label="Duplicate leg"><Copy /></button><button onClick={onRemove} disabled={total === 1} aria-label="Delete leg" className="danger"><Trash2 /></button></div>
    </div>
    {open && <div className="leg-body" ref={body}>
      <div className="leg-grid primary-fields"><NumberField label="Lots" min={1} value={leg.lots} onChange={v => onUpdate({ lots: v || 1 })} /><Segmented label="Position" value={leg.position} onChange={v => onUpdate({ position: v as "buy" | "sell" })} options={[{ value: "buy", label: "Buy" }, { value: "sell", label: "Sell" }]} /><Segmented label="Option type" value={leg.optionType} onChange={v => onUpdate({ optionType: v as "call" | "put" })} options={[{ value: "call", label: "Call" }, { value: "put", label: "Put" }]} /><Field label="Expiry"><input type="date" min={new Date().toISOString().slice(0,10)} value={leg.expiry} onChange={e => onUpdate({ expiry: e.target.value })} /></Field><Select label="Strike criteria" value={leg.strikeMode} onChange={v => onUpdate({ strikeMode: v as StrategyLeg["strikeMode"] })} options={["atm", "itm", "otm", "exact"]} />{leg.strikeMode === "exact" ? <OptionalNumber label="Exact strike" value={leg.exactStrike} onChange={v => onUpdate({ exactStrike: v })} /> : <NumberField label="Strike steps" min={0} max={100} value={leg.strikeSteps} onChange={v => onUpdate({ strikeSteps: v })} />}</div>
      <div className="subsection-label">Order & protection</div>
      <div className="leg-grid risk-fields"><Segmented label="Order type" value={leg.orderType} onChange={v => onUpdate({ orderType: v as "market_order" | "limit_order" })} options={[{ value: "market_order", label: "Market" }, { value: "limit_order", label: "Limit" }]} />{leg.orderType === "limit_order" && <Field label="Limit price"><input inputMode="decimal" value={leg.limitPrice || ""} onChange={e => onUpdate({ limitPrice: e.target.value || undefined })} placeholder="0.00" /></Field>}<OptionalNumber label="Target profit" value={leg.targetProfit} onChange={v => onUpdate({ targetProfit: v })} /><OptionalNumber label={`Stop loss${leg.position === "sell" ? " (required)" : ""}`} value={leg.stopLoss} onChange={v => onUpdate({ stopLoss: v })} /><OptionalNumber label="Trail SL" value={leg.trailStop} onChange={v => onUpdate({ trailStop: v })} /><NumberField label="Re-entry on target" min={0} max={10} value={leg.reentryOnTarget} onChange={v => onUpdate({ reentryOnTarget: v })} /><NumberField label="Re-entry on SL" min={0} max={10} value={leg.reentryOnStop} onChange={v => onUpdate({ reentryOnStop: v })} /></div>
      <p className="worker-note"><Activity /> Re-entry values are saved for review but are not automatically monitored by this worker version.</p>
    </div>}
  </article>;
}

function PreviewDrawer({ preview, strategy, onClose, onNotice }: { preview: PreviewData; strategy: StrategyDefinition; onClose: () => void; onNotice: (n: { tone: "ok" | "error"; text: string }) => void }) {
  const drawer = useRef<HTMLDivElement>(null); const [busy, setBusy] = useState<string | null>(null); const [confirmExecute, setConfirmExecute] = useState(false); const [confirmText, setConfirmText] = useState(""); const [error, setError] = useState("");
  useGSAP(() => { if (matchMedia("(prefers-reduced-motion: reduce)").matches) return; gsap.from(".drawer-backdrop", { opacity: 0, duration: .25 }); gsap.from(".preview-drawer", { xPercent: 100, duration: .5, ease: "power3.out" }); }, { scope: drawer });
  useEffect(() => { const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); }; addEventListener("keydown", handler); return () => removeEventListener("keydown", handler); }, [onClose]);

  async function save(status: "draft" | "scheduled", execute = false) {
    setBusy(execute ? "execute" : status); setError("");
    try {
      const saved = await requestJson<{ result: { id: string } }>("/api/strategies", { method: "POST", body: JSON.stringify({ strategy, status }) });
      if (execute) await requestJson(`/api/strategies/${saved.result.id}/execute`, { method: "POST" });
      onNotice({ tone: "ok", text: execute ? "Execution submitted. Monitor fills in Dashboard." : status === "scheduled" ? "Strategy scheduled successfully." : "Draft saved successfully." }); onClose();
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(null); }
  }

  return <div className="drawer-layer" ref={drawer} role="dialog" aria-modal="true" aria-labelledby="preview-title"><button className="drawer-backdrop" onClick={onClose} aria-label="Close preview" /><aside className="preview-drawer">
    <header><div><div className="eyebrow"><span /> Live contract resolution</div><h2 id="preview-title">Review before action</h2><p>Prices are a point-in-time preview and may change before fill.</p></div><button className="icon-button" onClick={onClose} aria-label="Close"><X /></button></header>
    <div className="preview-summary"><div><span>Strategy</span><strong>{strategy.name}</strong></div><div><span>Schedule</span><strong>{formatDateTime(strategy.entry.entryAt)}</strong></div><div><span>Square-off</span><strong>{strategy.squareOff}</strong></div></div>
    <div className="resolved-list"><div className="resolved-head"><span>Resolved legs</span><span>{preview.legs.length} contracts</span></div>{preview.legs.map((leg, i) => <div className="resolved-leg" key={leg.id}><span className="leg-number">{String(i + 1).padStart(2, "0")}</span><div><strong>{leg.productSymbol}</strong><span>{leg.position.toUpperCase()} {leg.lots} · {leg.orderType === "market_order" ? "Market" : `Limit ${leg.limitPrice}`}</span></div><div><small>Strike</small><strong>{leg.strike.toLocaleString()}</strong></div><div><small>Mark</small><strong>{leg.markPrice ?? "Unavailable"}</strong></div></div>)}</div>
    <div className="warning-box"><AlertTriangle /><div><strong>Execution conditions</strong>{preview.warnings.map(w => <p key={w}>{w}</p>)}</div></div>
    {error && <div className="inline-error"><AlertTriangle />{error}</div>}
    {confirmExecute && <div className="execute-confirm"><ShieldCheck /><div><strong>Confirm live execution</strong><p>This saves the strategy and immediately begins sequential order submission. In Production, this places orders using real funds. Type <b>EXECUTE</b> to continue.</p><input aria-label="Type EXECUTE to confirm" value={confirmText} onChange={e => setConfirmText(e.target.value.toUpperCase())} placeholder="Type EXECUTE" autoComplete="off" /><div><button className="ghost-button" onClick={() => { setConfirmExecute(false); setConfirmText(""); }}>Go back</button><button className="danger-button" onClick={() => void save("draft", true)} disabled={Boolean(busy) || confirmText !== "EXECUTE"}>{busy === "execute" ? <LoaderCircle className="spin" /> : <Zap />}Confirm & execute</button></div></div></div>}
    <footer><button className="ghost-button" onClick={() => void save("draft")} disabled={Boolean(busy)}>{busy === "draft" ? <LoaderCircle className="spin" /> : <Save />}Save draft</button><button className="secondary-button" onClick={() => void save("scheduled")} disabled={Boolean(busy)}>{busy === "scheduled" ? <LoaderCircle className="spin" /> : <Clock3 />}Schedule</button><button className="primary-button" onClick={() => setConfirmExecute(true)} disabled={Boolean(busy) || confirmExecute}><Zap />Execute now</button></footer>
  </aside></div>;
}

function Dashboard({ onNotice }: { onNotice: (n: { tone: "ok" | "error"; text: string }) => void }) {
  const [data, setData] = useState<Overview | null>(null); const [loading, setLoading] = useState(true); const [cancel, setCancel] = useState<{ id: string; productId: number; symbol: string } | null>(null);
  const load = useCallback(async () => { setLoading(true); try { setData(await requestJson<Overview>("/api/account/overview")); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } finally { setLoading(false); } }, [onNotice]);
  useEffect(() => { void load(); }, [load]);
  const balance = useMemo(() => totalBalance(data?.balances || []), [data]);
  async function cancelOrder() { if (!cancel) return; try { await requestJson(`/api/orders/${cancel.id}`, { method: "DELETE", body: JSON.stringify({ productId: cancel.productId, confirm: true }) }); onNotice({ tone: "ok", text: `Order ${cancel.id} cancelled.` }); setCancel(null); void load(); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } }
  return <div className="dashboard-page"><section className="page-heading" data-reveal><div><div className="eyebrow"><span /> Account overview</div><h1>Trading dashboard</h1><p>Live balances, positions, and outstanding Delta orders.</p></div><button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} />Refresh</button></section>
    {loading ? <PanelSkeleton /> : <><div className="metric-grid"><Metric icon={<Wallet />} label="Estimated balance" value={balance} note="Across returned assets" /><Metric icon={<TrendingUp />} label="Open positions" value={String(data?.positions.length || 0)} note="Live Delta positions" /><Metric icon={<Clock3 />} label="Open orders" value={String(data?.orders.length || 0)} note="Awaiting fill or cancellation" /></div><DataPanel title="Open positions" icon={<BarChart3 />} rows={data?.positions || []} empty="No open positions" /><DataPanel title="Open orders" icon={<Clock3 />} rows={data?.orders || []} empty="No open orders" action={(row) => { const id = String(row.id ?? row.order_id ?? ""); const productId = Number(row.product_id); if (id && productId) setCancel({ id, productId, symbol: String(row.product_symbol ?? row.symbol ?? id) }); }} /></>}
    {cancel && <ConfirmModal title="Cancel open order?" description={`Order ${cancel.symbol} will be cancelled on Delta Exchange. Filled quantities cannot be reversed.`} confirm="Cancel order" onClose={() => setCancel(null)} onConfirm={() => void cancelOrder()} />}
  </div>;
}

function RunHistory({ onNotice }: { onNotice: (n: { tone: "ok" | "error"; text: string }) => void }) {
  const [rows, setRows] = useState<StrategyRow[]>([]); const [loading, setLoading] = useState(true); const [cancel, setCancel] = useState<StrategyRow | null>(null);
  const load = useCallback(async () => { setLoading(true); try { const data = await requestJson<{ result: StrategyRow[] }>("/api/strategies"); setRows(data.result); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } finally { setLoading(false); } }, [onNotice]);
  useEffect(() => { void load(); }, [load]);
  async function cancelStrategy() { if (!cancel) return; try { await requestJson(`/api/strategies/${cancel.id}`, { method: "DELETE" }); onNotice({ tone: "ok", text: `${cancel.name} cancelled.` }); setCancel(null); void load(); } catch (e) { onNotice({ tone: "error", text: errorMessage(e) }); } }
  return <div><section className="page-heading" data-reveal><div><div className="eyebrow"><span /> Strategy operations</div><h1>Run history</h1><p>Review scheduled, active, completed, and attention-required strategies.</p></div><button className="secondary-button" onClick={() => void load()}><RefreshCw className={loading ? "spin" : ""} />Refresh</button></section><section className="panel run-panel" data-reveal>{loading ? <PanelSkeleton /> : rows.length ? <div className="run-list">{rows.map(row => <article className="run-row" key={row.id}><span className={`status-dot ${row.status}`} /><div><strong>{row.name}</strong><span>Created {formatDateTime(row.createdAt)}</span></div><span className={`status-chip ${row.status}`}>{row.status.replaceAll("_", " ")}</span><div><small>Entry</small><strong>{formatDateTime(row.entryAt)}</strong></div><div><small>Exit</small><strong>{formatDateTime(row.exitAt)}</strong></div>{row.lastError && <span className="row-error" title={row.lastError}><AlertTriangle />Needs attention</span>}<button className="icon-button danger" disabled={!['draft','scheduled'].includes(row.status)} onClick={() => setCancel(row)} aria-label={`Cancel ${row.name}`}><Trash2 /></button></article>)}</div> : <div className="empty-state"><Activity /><h3>No strategy runs yet</h3><p>Saved and scheduled strategies will appear here.</p></div>}</section>{cancel && <ConfirmModal title="Cancel strategy?" description={`${cancel.name} will no longer run. Existing fills, if any, are not reversed.`} confirm="Cancel strategy" onClose={() => setCancel(null)} onConfirm={() => void cancelStrategy()} />}</div>;
}

function Metric({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) { return <div className="metric panel" data-reveal><span>{icon}</span><div><small>{label}</small><strong>{value}</strong><p>{note}</p></div></div>; }
function PanelSkeleton() { return <div className="panel skeleton-wrap"><div className="skeleton wide" /><div className="skeleton" /><div className="skeleton" /><div className="skeleton short" /></div>; }

function DataPanel({ title, icon, rows, empty, action }: { title: string; icon: React.ReactNode; rows: Record<string, unknown>[]; empty: string; action?: (row: Record<string, unknown>) => void }) {
  const columns = rows.length ? Object.keys(rows[0]).filter(k => ["symbol","product_symbol","side","size","entry_price","mark_price","limit_price","state","unrealized_pnl","id"].includes(k)).slice(0, 6) : [];
  return <section className="panel data-panel" data-reveal><div className="panel-title"><div><span className="heading-icon">{icon}</span><div><h2>{title}</h2><p>{rows.length} live {rows.length === 1 ? "record" : "records"}</p></div></div></div>{rows.length ? <div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{c.replaceAll("_", " ")}</th>)}{action && <th>Action</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? index)}>{columns.map(c => <td key={c}>{formatCell(row[c])}</td>)}{action && <td><button className="text-danger" onClick={() => action(row)}>Cancel</button></td>}</tr>)}</tbody></table></div> : <div className="empty-state compact"><Activity /><h3>{empty}</h3><p>Delta will report new activity here.</p></div>}</section>;
}

function ConfirmModal({ title, description, confirm, onClose, onConfirm }: { title: string; description: string; confirm: string; onClose: () => void; onConfirm: () => void }) { return <div className="modal-layer" role="dialog" aria-modal="true"><button className="drawer-backdrop" onClick={onClose} aria-label="Close confirmation" /><div className="confirm-modal"><span className="danger-icon"><AlertTriangle /></span><h2>{title}</h2><p>{description}</p><div><button className="ghost-button" onClick={onClose}>Keep it</button><button className="danger-button" onClick={onConfirm}>{confirm}</button></div></div></div>; }

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="field"><span>{label}</span>{children}</label>; }
function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) { return <Field label={label}><span className="select-wrap"><select value={value} onChange={e => onChange(e.target.value)}>{options.map(o => <option value={o} key={o}>{o.toUpperCase()}</option>)}</select><ChevronDown /></span></Field>; }
function NumberField({ label, value, min = 0, max, onChange }: { label: string; value: number; min?: number; max?: number; onChange: (v: number) => void }) { return <Field label={label}><input type="number" value={value} min={min} max={max} onChange={e => onChange(Number(e.target.value))} /></Field>; }
function OptionalNumber({ label, value, onChange }: { label: string; value?: number; onChange: (v?: number) => void }) { return <Field label={label}><input type="number" min="0" step="any" value={value ?? ""} onChange={e => onChange(e.target.value === "" ? undefined : Number(e.target.value))} placeholder="Disabled" /></Field>; }
function Segmented({ label, value, options, onChange, disabled }: { label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; disabled?: boolean }) { return <fieldset className="segmented-field" disabled={disabled}><legend>{label}</legend><div>{options.map(o => <button type="button" className={value === o.value ? "active" : ""} key={o.value} onClick={() => onChange(o.value)}>{o.label}</button>)}</div></fieldset>; }
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) { return <label className="toggle-field"><span>{label}</span><button type="button" role="switch" aria-checked={checked} className={checked ? "toggle on" : "toggle"} onClick={() => onChange(!checked)}><i /></button></label>; }

function formatDate(value: string) { return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "2-digit" }).format(new Date(`${value}T00:00:00`)); }
function formatDateTime(value: string) { if (!value) return "—"; return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function formatCell(value: unknown) { if (value == null) return "—"; if (typeof value === "object") return JSON.stringify(value); return String(value); }
function totalBalance(rows: unknown[]) { let total = 0; for (const item of rows) { if (item && typeof item === "object") { const r = item as Record<string, unknown>; const value = Number(r.balance ?? r.available_balance ?? r.wallet_balance ?? 0); if (Number.isFinite(value)) total += value; } } return total ? `$${total.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"; }
