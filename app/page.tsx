"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { RefreshCw, WifiOff } from "@/app/components/icons";
import { ThinkingOrb } from "thinking-orbs";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { reportSessionEvent, requestJson, resetApiOrigin, SESSION_AUTH_EVENT } from "@/lib/api";
import { useRealtimeSignals } from "@/app/components/RealtimeSignals";
import { errorMessage } from "@/lib/format";
import type { Account, AppUser } from "@/lib/app-types";
import { readWorkspaceSession, signOutThisDevice, type WorkspaceSession } from "@/lib/workspace-session";
import { AppShell, TAB_ORDER, type ConnectionState, type Tab } from "@/app/components/AppShell";
import AuthView from "@/app/components/AuthView";
import ConnectView from "@/app/components/ConnectView";
import StrategyBuilder from "@/app/components/StrategyBuilder";
import Dashboard from "@/app/components/Dashboard";
import RunHistory from "@/app/components/RunHistory";
import {
  Brand, ConfirmModal, LearnMoreChevron, PageEnter, Shimmer, TableSkeleton, Toast,
  useTravelDirection, type Notice
} from "@/app/components/ui";

/**
 * The research surfaces carry the heaviest dependencies — an SVG charting
 * pipeline and a Markdown renderer — and neither is on the path an operator
 * takes to schedule a strategy, so both load when their tab is first opened.
 */
const BtcMarketChart = dynamic(() => import("@/app/components/BtcMarketChart"), {
  loading: () => <TableSkeleton label="market analysis" rows={8} />
});

const Automation = dynamic(() => import("@/app/components/Automation"), {
  loading: () => <TableSkeleton label="automation" rows={6} />
});

const PnlView = dynamic(() => import("@/app/components/PnlView"), {
  loading: () => <TableSkeleton label="P&L" rows={6} />
});

const OwnerUsers = dynamic(() => import("@/app/components/OwnerUsers"), {
  loading: () => <TableSkeleton label="users" rows={6} />
});

const ProfileExchangeAccounts = dynamic(
  () => import("@/app/components/ProfileExchangeAccounts").then(module => module.ProfileExchangeAccounts),
  { loading: () => <TableSkeleton label="sub accounts" rows={4} /> }
);

type BackendStatus = "checking" | "online" | "offline";

const CONNECTED_TABS: Tab[] = ["builder", "runs", "dashboard", "market", "automation", "pnl", "subaccounts"];
const UNCONNECTED_TABS: Tab[] = ["connect", "builder", "market", "pnl", "subaccounts"];
const OFFLINE_TABS: Tab[] = ["builder", "market"];

/**
 * The Users page is listed only when the backend session says this account is the
 * owner. The backend checks the role again on every owner request, so this is
 * navigation, not authorization.
 */
function tabsFor(base: Tab[], isOwner: boolean): Tab[] {
  return isOwner && base.includes("pnl") ? [...base, "users"] : base;
}

/**
 * Where a session lands when nothing has been picked yet, or when the picked
 * tab stops being available: the portfolio once Delta is connected, the
 * connection step when it can be completed, and public market data otherwise.
 */
function landingTab(connected: boolean, backendStatus: BackendStatus): Tab {
  if (connected) return "dashboard";
  return backendStatus === "online" ? "connect" : "market";
}

/** Cheap enough to run alongside the run list without straining rate limits. */
const ATTENTION_POLL_MS = 60_000;

/**
 * Session orchestration.
 *
 * Three states are possible and each gets its own surface: not signed in,
 * signed in without a Delta connection, and fully connected. A fourth
 * fallback — signed in while the trading backend is unreachable — keeps the
 * builder usable in design mode so work is never lost behind an offline API.
 */
export default function Home() {
  const { automation: automationRevision, strategies: strategyRevision } = useRealtimeSignals();
  const [workspace, setWorkspace] = useState<WorkspaceSession>({ kind: "loading" });
  const currentSession = useRef<WorkspaceSession>(workspace);
  const loadVersion = useRef(0);
  const loadController = useRef<AbortController | null>(null);
  const lastCheck = useRef(0);
  const user = workspace.kind === "ready" || workspace.kind === "unavailable" ? workspace.user : null;
  const account = workspace.kind === "ready" || workspace.kind === "unavailable" ? workspace.account : null;
  const backendStatus: BackendStatus = workspace.kind === "loading" ? "checking"
    : workspace.kind === "ready" ? "online" : "offline";
  // Null until the operator picks a section, so the landing surface follows the
  // session state once it is known instead of a hard-coded default.
  const [selectedTab, setTab] = useState<Tab | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [attention, setAttention] = useState(0);

  const applySession = useCallback((next: WorkspaceSession) => {
    currentSession.current = next;
    setWorkspace(next);
  }, []);

  const invalidateSession = useCallback(() => {
    ++loadVersion.current;
    loadController.current?.abort();
  }, []);

  const loadSession = useCallback(async () => {
    const version = ++loadVersion.current;
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    lastCheck.current = Date.now();
    try {
      const next = await readWorkspaceSession(currentSession.current,
        AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]));
      if (version === loadVersion.current) applySession(next);
    } catch {
      // Cancellation discards an old user's response. A timeout keeps the last known identity.
      if (version === loadVersion.current && !controller.signal.aborted) {
        const old = currentSession.current;
        applySession({ kind: "unavailable", user: "user" in old ? old.user : null,
          account: "account" in old ? old.account : null });
      }
    }
  }, [applySession]);

  useEffect(() => {
    let scheduled: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      clearTimeout(scheduled);
      // Keep Supabase calls outside its auth callback/refresh lock.
      scheduled = setTimeout(() => { void loadSession(); }, 0);
    };
    const { data } = getSupabaseBrowserClient().auth.onAuthStateChange((event, session) => {
      const current = currentSession.current;
      if (event === "SIGNED_OUT") {
        invalidateSession();
        clearTimeout(scheduled);
        applySession({ kind: "signed-out" });
        setTab(null);
        setConfirmDisconnect(false);
        setAttention(0);
        resetApiOrigin();
        return;
      }
      const sameUser = "user" in current && current.user?.id === session?.user.id;
      if (event === "SIGNED_IN" && sameUser && current.kind === "ready") return;
      if (event === "SIGNED_IN" && !sameUser) {
        invalidateSession();
        applySession({ kind: "loading" });
        setTab(null);
        setConfirmDisconnect(false);
        setNotice(null);
      }
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") {
        if (session) {
          const name = event === "SIGNED_IN" ? "signed_in" : "token_refreshed";
          setTimeout(() => reportSessionEvent(name, session.access_token), 0);
        }
      }
      if (event === "INITIAL_SESSION" || event === "SIGNED_IN" || event === "TOKEN_REFRESHED"
        || event === "USER_UPDATED") schedule();
    });
    const recheck = (event: Event) => {
      const current = currentSession.current;
      const detail: unknown = event instanceof CustomEvent ? event.detail : null;
      if ("user" in current && current.user && detail && typeof detail === "object"
        && "userId" in detail && (detail.userId === current.user.id || detail.userId === null)
        && Date.now() - lastCheck.current > 5_000) schedule();
    };
    const resume = () => {
      if (document.visibilityState === "visible" && Date.now() - lastCheck.current > 30_000) schedule();
    };
    window.addEventListener(SESSION_AUTH_EVENT, recheck);
    window.addEventListener("online", schedule);
    document.addEventListener("visibilitychange", resume);
    schedule();
    return () => {
      invalidateSession();
      clearTimeout(scheduled);
      data.subscription.unsubscribe();
      window.removeEventListener(SESSION_AUTH_EVENT, recheck);
      window.removeEventListener("online", schedule);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [applySession, invalidateSession, loadSession]);

  useEffect(() => {
    if (workspace.kind !== "unavailable") return;
    const timer = setTimeout(() => { void loadSession(); }, 15_000);
    return () => clearTimeout(timer);
  }, [loadSession, workspace]);

  const setAccount = (next: Account | null) => {
    ++loadVersion.current;
    loadController.current?.abort();
    const current = currentSession.current;
    if ("user" in current && current.user) applySession({ kind: "ready", user: current.user, account: next });
  };

  // A tapped phone notification opens `/?tab=runs`; honour it once, then clean the URL.
  useEffect(() => {
    const url = new URL(window.location.href);
    const requested = url.searchParams.get("tab");
    const known = [...CONNECTED_TABS, ...UNCONNECTED_TABS].find(item => item === requested);
    if (!requested) return;
    if (known) setTab(known);
    url.searchParams.delete("tab");
    url.searchParams.delete("strategy");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);

  const connected = backendStatus === "online" && Boolean(account);
  const isOwner = backendStatus === "online" && user?.userType === "owner";
  const availableTabs = tabsFor(
    connected ? CONNECTED_TABS : backendStatus === "online" ? UNCONNECTED_TABS : OFFLINE_TABS,
    isOwner
  );

  const tab = selectedTab && availableTabs.includes(selectedTab)
    ? selectedTab
    : landingTab(connected, backendStatus);

  /**
   * Runs that did not complete cleanly are surfaced on the navigation itself, so
   * a failed entry is noticed while the operator is on another surface rather
   * than only when they happen to open the run list.
   *
   * Run history reports its own count up whenever it loads, so opening that tab
   * or acting on a row updates the badge immediately; this slower poll only
   * covers the case where the tab has never been opened. It is deliberately
   * quieter than the 30-second list refresh because it is background awareness,
   * not a live view.
   */
  useEffect(() => {
    if (!connected) {
      setAttention(0);
      return;
    }
    let cancelled = false;

    const count = async () => {
      try {
        const counts = await requestJson<{ attention: number }>("/api/strategies/counts").catch(() => null);
        const attention = counts?.attention ?? (await requestJson<{ result: { status: string }[] }>("/api/strategies"))
          .result.filter(run => run.status === "attention").length;
        if (!cancelled) setAttention(attention);
      } catch {
        // Background awareness only: a failed count must never raise a toast.
      }
    };

    void count();
    const timer = window.setInterval(
      () => { void count(); },
      automationRevision || strategyRevision ? 300_000 : ATTENTION_POLL_MS
    );
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [automationRevision, connected, strategyRevision]);

  async function disconnectDelta() {
    try {
      await requestJson("/api/session", { method: "DELETE" });
      setAccount(null);
      setConfirmDisconnect(false);
      setTab("connect");
      setNotice({ tone: "ok", text: "Delta Exchange disconnected. Your Trade Cognition account remains signed in." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  async function signOut() {
    try {
      const { data } = await getSupabaseBrowserClient().auth.getSession();
      if (data.session) reportSessionEvent("sign_out_local", data.session.access_token);
      await signOutThisDevice();
      ++loadVersion.current;
      loadController.current?.abort();
      applySession({ kind: "signed-out" });
      setTab(null);
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  if (workspace.kind === "loading") return <LoadingScreen />;

  if (workspace.kind === "unavailable" && !user) return (
    <div className="boot-screen"><Brand /><OfflineBanner onRetry={loadSession} /></div>
  );

  if (!user) return <><AuthView onAuthenticated={loadSession} />
    {notice && <div className="toast-region"><Toast notice={notice} onClose={() => setNotice(null)} /></div>}</>;

  const connection: ConnectionState = connected
    ? { label: "Delta connected", detail: "Exchange account linked", tone: "active" }
    : backendStatus === "online"
      ? { label: "Delta not connected", detail: "Research is available", tone: "neutral" }
      : { label: "Trading services unavailable", detail: "Public market data is available", tone: "warning" };
  return (
    <>
      <AppShell
        tab={tab}
        availableTabs={availableTabs}
        connection={connection}
        account={{
          name: account?.accountName || user.displayName || "Account",
          detail: account?.email || user.email || "Signed in",
          avatarUrl: user.avatarUrl,
          role: isOwner ? "owner" : "user"
        }}
        badges={{ runs: attention }}
        onNavigate={setTab}
        onDisconnect={connected ? () => setConfirmDisconnect(true) : undefined}
        onSignOut={signOut}
        banner={backendStatus === "offline" ? <OfflineBanner onRetry={loadSession} /> : undefined}
      >
        <WorkspaceBody
          key={user.id}
          tab={tab}
          connected={connected}
          backendOnline={backendStatus === "online"}
          user={user}
          userId={user.id}
          onNotice={setNotice}
          onAttention={setAttention}
          onConnected={next => {
            setAccount(next);
            setTab("dashboard");
            setNotice({ tone: "ok", text: "Delta Exchange connected securely." });
          }}
          onSignOut={signOut}
          onConnect={() => setTab("connect")}
          onManageCapital={() => setTab("subaccounts")}
        />
      </AppShell>

      {notice && (
        <div className="toast-region">
          {/* Keyed on the message so a second notice replays the entrance instead
              of silently swapping text inside a toast that is already open. */}
          <Toast key={`${notice.tone}:${notice.text}`} notice={notice} onClose={() => setNotice(null)} />
        </div>
      )}

      {confirmDisconnect && (
        <ConfirmModal
          title="Disconnect Delta Exchange?"
          description="Your saved Delta credentials will be permanently deleted. Your Trade Cognition account and saved strategies will remain."
          confirm="Disconnect Delta"
          cancel="Stay connected"
          onClose={() => setConfirmDisconnect(false)}
          onConfirm={() => void disconnectDelta()}
        />
      )}
    </>
  );
}

/**
 * The workspace body.
 *
 * Exactly one section is mounted at a time, so switching tabs plays the
 * entrance half of the side-by-side page slide: the incoming surface enters
 * from the side it travelled from, which keeps the sections feeling spatially
 * arranged rather than swapped at random. `PageEnter` is keyed on the tab, so
 * React remounts the body and the animation restarts on every move.
 */
function WorkspaceBody({ tab, connected, backendOnline, user, userId, onNotice, onAttention, onConnected, onSignOut, onConnect, onManageCapital }: {
  tab: Tab;
  connected: boolean;
  backendOnline: boolean;
  user: AppUser;
  userId: string;
  onNotice: (notice: Notice) => void;
  onAttention: (count: number) => void;
  onConnected: (account: Account) => void;
  onSignOut: () => void;
  onConnect: () => void;
  onManageCapital: () => void;
}) {
  const direction = useTravelDirection(tab, TAB_ORDER);

  return (
    // The key is what remounts the body, which is what restarts the entrance.
    <PageEnter key={tab} direction={direction}>
      {tab === "connect" && backendOnline && !connected && (
        <ConnectView user={user} onConnected={onConnected} onSignOut={onSignOut} embedded />
      )}
      {tab === "builder" && <StrategyBuilder isOwner={user.userType === "owner"} userId={userId} onNotice={onNotice} liveEnabled={connected} backendOnline={backendOnline} />}
      {tab === "runs" && connected && <RunHistory onNotice={onNotice} onAttentionChange={onAttention} />}
      {tab === "dashboard" && connected && <Dashboard onNotice={onNotice} onManageCapital={onManageCapital} />}
      {tab === "market" && <BtcMarketChart />}
      {tab === "automation" && connected && backendOnline && <Automation isOwner={user.userType === "owner"} onNotice={onNotice} onManageCapital={onManageCapital} />}
      {tab === "pnl" && backendOnline && <PnlView />}
      {tab === "subaccounts" && backendOnline && <ProfileExchangeAccounts connected={connected} onConnect={onConnect} />}
      {tab === "users" && backendOnline && user.userType === "owner" && <OwnerUsers onNotice={onNotice} />}
    </PageEnter>
  );
}

/**
 * Boot surface.
 *
 * The orb states what the app is actually doing — restoring a session — instead
 * of a spinner that only says "busy", and the copy rises with the staggered text
 * reveal so the first screen reads as a sequence rather than a flash.
 */
function LoadingScreen() {
  return (
    <div className="boot-screen t-stagger is-shown" role="status">
      <span className="t-stagger-line t-stagger-line--1"><Brand /></span>
      <span className="agent-orb t-stagger-line t-stagger-line--2">
        <ThinkingOrb state="connecting" size={64} theme="auto" aria-label="Restoring your secure session" />
      </span>
      <div className="boot-copy">
        <strong className="t-stagger-line t-stagger-line--3">
          <Shimmer>Restoring your secure session</Shimmer>
        </strong>
        <small className="t-stagger-line t-stagger-line--4">Signing you in and loading your account</small>
      </div>
      <div className="boot-progress t-stagger-line t-stagger-line--5" aria-hidden="true"><i /></div>
    </div>
  );
}

/**
 * Design mode explains precisely which capabilities are unavailable, so the
 * absence of live data reads as a known state rather than a failure.
 */
function OfflineBanner({ onRetry }: { onRetry: () => Promise<void> }) {
  // A panel sliding into a region of the page: it opens on the slower panel
  // clock with a cross-blur, so losing the backend announces itself rather than
  // a banner simply existing on the next paint.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <div className="callout tone-warning t-panel-slide" data-open={open} role="status">
      <WifiOff aria-hidden="true" />
      <span>
        <strong>Live trading is temporarily unavailable.</strong>
        {" "}Local drafts, saved recovery state, and public market data remain available where cached.
      </span>
      <button type="button" className="button secondary t-learn" onClick={() => void onRetry()}>
        <RefreshCw aria-hidden="true" />Retry<LearnMoreChevron />
      </button>
    </div>
  );
}
