"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { RefreshCw, WifiOff } from "lucide-react";
import { ThinkingOrb } from "thinking-orbs";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { requestJson, resetApiOrigin } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import type { Account, AppUser, SessionResponse, StrategyRun } from "@/lib/app-types";
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

const NewsAnalysis = dynamic(() => import("@/app/components/NewsAnalysis"), {
  loading: () => <TableSkeleton label="news intelligence" rows={6} />
});

type BackendStatus = "checking" | "online" | "offline";

const CONNECTED_TABS: Tab[] = ["builder", "runs", "dashboard", "market", "news"];
const UNCONNECTED_TABS: Tab[] = ["connect", "builder", "market", "news"];
const OFFLINE_TABS: Tab[] = ["builder", "market"];

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
  const [sessionLoading, setSessionLoading] = useState(true);
  const [user, setUser] = useState<AppUser | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");
  const [tab, setTab] = useState<Tab>("builder");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [attention, setAttention] = useState(0);

  const loadSession = useCallback(async () => {
    try {
      const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
      if (!session?.user) {
        setUser(null);
        setAccount(null);
        setBackendStatus("offline");
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
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSessionLoading(false);
    }
  }, []);

  useEffect(() => { void loadSession(); }, [loadSession]);

  const connected = Boolean(account);
  const availableTabs = connected
    ? CONNECTED_TABS
    : backendStatus === "online"
      ? UNCONNECTED_TABS
      : OFFLINE_TABS;

  useEffect(() => {
    if (availableTabs.includes(tab)) return;
    setTab(backendStatus === "online" && !connected ? "connect" : "builder");
  }, [availableTabs, backendStatus, connected, tab]);

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
        const data = await requestJson<{ result: StrategyRun[] }>("/api/strategies");
        if (!cancelled) setAttention(data.result.filter(run => run.status === "attention").length);
      } catch {
        // Background awareness only: a failed count must never raise a toast.
      }
    };

    void count();
    const timer = window.setInterval(() => { void count(); }, ATTENTION_POLL_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [connected]);

  async function disconnectDelta() {
    try {
      await requestJson("/api/session", { method: "DELETE" });
      setAccount(null);
      setConfirmDisconnect(false);
      setTab("connect");
      setNotice({ tone: "ok", text: "Delta Exchange disconnected. Your workspace account remains signed in." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  async function signOut() {
    try {
      await getSupabaseBrowserClient().auth.signOut();
      setUser(null);
      setAccount(null);
      setBackendStatus("offline");
      setTab("builder");
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  if (sessionLoading) return <LoadingScreen />;

  if (!user) return <AuthView onAuthenticated={loadSession} />;

  const connection: ConnectionState = connected
    ? { label: "Delta connected", detail: "Live production venue", tone: "active" }
    : backendStatus === "online"
      ? { label: "Delta not connected", detail: "Research is available", tone: "neutral" }
      : { label: "Trading API unavailable", detail: "Public market remains available", tone: "warning" };
  return (
    <>
      <AppShell
        tab={tab}
        availableTabs={availableTabs}
        connection={connection}
        account={{
          name: account?.accountName || user.displayName || "Workspace",
          detail: account?.email || user.email || "Signed in"
        }}
        badges={{ runs: attention }}
        onNavigate={setTab}
        onDisconnect={connected ? () => setConfirmDisconnect(true) : undefined}
        onSignOut={signOut}
        banner={backendStatus === "offline" ? <OfflineBanner onRetry={loadSession} /> : undefined}
      >
        <WorkspaceBody
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
          description="The stored Delta API secret is permanently removed from Vault. Your workspace account and saved strategies remain."
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
function WorkspaceBody({ tab, connected, backendOnline, user, userId, onNotice, onAttention, onConnected, onSignOut }: {
  tab: Tab;
  connected: boolean;
  backendOnline: boolean;
  user: AppUser;
  userId: string;
  onNotice: (notice: Notice) => void;
  onAttention: (count: number) => void;
  onConnected: (account: Account) => void;
  onSignOut: () => void;
}) {
  const direction = useTravelDirection(tab, TAB_ORDER);

  return (
    // The key is what remounts the body, which is what restarts the entrance.
    <PageEnter key={tab} direction={direction}>
      {tab === "connect" && backendOnline && !connected && (
        <ConnectView user={user} onConnected={onConnected} onSignOut={onSignOut} embedded />
      )}
      {tab === "builder" && <StrategyBuilder userId={userId} onNotice={onNotice} liveEnabled={connected} />}
      {tab === "runs" && connected && <RunHistory onNotice={onNotice} onAttentionChange={onAttention} />}
      {tab === "dashboard" && connected && <Dashboard onNotice={onNotice} />}
      {tab === "market" && <BtcMarketChart />}
      {tab === "news" && backendOnline && <NewsAnalysis request={requestJson} />}
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
      <span className="news-orb t-stagger-line t-stagger-line--2">
        <ThinkingOrb state="connecting" size={64} theme="dark" aria-label="Restoring your secure session" />
      </span>
      <div className="boot-copy">
        <strong className="t-stagger-line t-stagger-line--3">
          <Shimmer>Restoring your secure session</Shimmer>
        </strong>
        <small className="t-stagger-line t-stagger-line--4">Checking the workspace account and the trading backend</small>
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
        <strong>Trading backend unreachable — design mode.</strong>
        {" "}Configuration, the saved library, and JSON export stay available. Contract resolution,
        scheduling, execution, portfolio, run history, and news need the trading API. Public market
        analysis remains available.
      </span>
      <button type="button" className="button secondary t-learn" onClick={() => void onRetry()}>
        <RefreshCw aria-hidden="true" />Retry<LearnMoreChevron />
      </button>
    </div>
  );
}
