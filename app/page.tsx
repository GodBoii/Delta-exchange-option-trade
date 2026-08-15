"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { LoaderCircle, RefreshCw, WifiOff } from "lucide-react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { requestJson, resetApiOrigin } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import type { Account, AppUser, SessionResponse } from "@/lib/app-types";
import { AppShell, type ConnectionState, type Tab } from "@/app/components/AppShell";
import AuthView from "@/app/components/AuthView";
import ConnectView from "@/app/components/ConnectView";
import StrategyBuilder from "@/app/components/StrategyBuilder";
import Dashboard from "@/app/components/Dashboard";
import RunHistory from "@/app/components/RunHistory";
import { Brand, ConfirmModal, TableSkeleton, Toast, type Notice } from "@/app/components/ui";

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
const DESIGN_TABS: Tab[] = ["builder"];

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

  async function disconnectDelta() {
    try {
      await requestJson("/api/session", { method: "DELETE" });
      setAccount(null);
      setConfirmDisconnect(false);
      setTab("builder");
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

  if (!account && backendStatus === "online") {
    return (
      <ConnectView
        user={user}
        onSignOut={signOut}
        onConnected={next => {
          setAccount(next);
          setNotice({ tone: "ok", text: "Delta Exchange connected securely." });
        }}
      />
    );
  }

  const connected = Boolean(account);
  const connection: ConnectionState = connected
    ? { label: "Delta connected", detail: "Live production venue", tone: "active" }
    : { label: "Design mode", detail: "Trading backend offline", tone: "warning" };

  return (
    <>
      <AppShell
        tab={tab}
        availableTabs={connected ? CONNECTED_TABS : DESIGN_TABS}
        connection={connection}
        account={{
          name: account?.accountName || user.displayName || "Workspace",
          detail: account?.email || user.email || "Signed in"
        }}
        onNavigate={setTab}
        onDisconnect={connected ? () => setConfirmDisconnect(true) : undefined}
        onSignOut={signOut}
        banner={connected ? undefined : <OfflineBanner onRetry={loadSession} />}
      >
        {tab === "builder" && <StrategyBuilder userId={user.id} onNotice={setNotice} liveEnabled={connected} />}
        {tab === "runs" && connected && <RunHistory onNotice={setNotice} />}
        {tab === "dashboard" && connected && <Dashboard onNotice={setNotice} />}
        {tab === "market" && connected && <BtcMarketChart />}
        {tab === "news" && connected && <NewsAnalysis request={requestJson} />}
      </AppShell>

      {notice && (
        <div className="toast-region">
          <Toast notice={notice} onClose={() => setNotice(null)} />
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

function LoadingScreen() {
  return (
    <div className="boot-screen" role="status">
      <Brand />
      <LoaderCircle className="spin" aria-hidden="true" />
      <p>Restoring your secure session</p>
    </div>
  );
}

/**
 * Design mode explains precisely which capabilities are unavailable, so the
 * absence of live data reads as a known state rather than a failure.
 */
function OfflineBanner({ onRetry }: { onRetry: () => Promise<void> }) {
  return (
    <div className="callout tone-warning" role="status">
      <WifiOff aria-hidden="true" />
      <span>
        <strong>Trading backend unreachable — design mode.</strong>
        {" "}Configuration, the saved library, and JSON export stay available. Contract resolution,
        scheduling, execution, portfolio, and research need the local Docker backend.
      </span>
      <button type="button" className="button secondary" onClick={() => void onRetry()}>
        <RefreshCw aria-hidden="true" />Retry
      </button>
    </div>
  );
}
