"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ConvexProviderWithAuth, ConvexReactClient, useConvexAuth, useQuery } from "convex/react";
import { api } from "@/convex/_generated/api";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { apiOrigin } from "@/lib/api";

type Revisions = { automation?: number; strategies?: number };
const RealtimeContext = createContext<Revisions>({});
const url = process.env.NEXT_PUBLIC_CONVEX_URL;
const client = url ? new ConvexReactClient(url) : null;
const localStorageEnabled = process.env.NEXT_PUBLIC_APPLICATION_STORAGE === "local";

function useSupabaseAuth() {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    void supabase.auth.getSession().then(({ data }) => {
      setAuthenticated(Boolean(data.session));
      setLoading(false);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setAuthenticated(Boolean(session));
      setLoading(false);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const fetchAccessToken = useCallback(async ({ forceRefreshToken }: { forceRefreshToken: boolean }) => {
    const supabase = getSupabaseBrowserClient();
    const result = forceRefreshToken ? await supabase.auth.refreshSession() : await supabase.auth.getSession();
    return result.data.session?.access_token ?? null;
  }, []);

  return useMemo(() => ({ isLoading: loading, isAuthenticated: authenticated, fetchAccessToken }), [authenticated, fetchAccessToken, loading]);
}

function SignalBridge({ onChange }: { onChange: (value: Revisions) => void }) {
  const { isAuthenticated } = useConvexAuth();
  const automation = useQuery(api.signals.latest, isAuthenticated ? { scope: "automation" } : "skip");
  const strategies = useQuery(api.signals.latest, isAuthenticated ? { scope: "strategies" } : "skip");

  useEffect(() => {
    onChange({ automation: automation?.updatedAt, strategies: strategies?.updatedAt });
  }, [automation?.updatedAt, onChange, strategies?.updatedAt]);
  return null;
}

function LocalSignalBridge({ onChange }: { onChange: (value: Revisions) => void }) {
  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();
    const connect = async () => {
      try {
        const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
        if (!session) return;
        const origin = await apiOrigin();
        const response = await fetch(`${origin}/api/revisions`, {
          headers: { Authorization: `Bearer ${session.access_token}` },
          cache: "no-store", signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error("Revision stream unavailable");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done || stopped) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            const event = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = event.split("\n").find(line => line.startsWith("data: "))?.slice(6);
            if (data) {
              try {
                const parsed: unknown = JSON.parse(data);
                if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                  const value = parsed as Record<string, unknown>;
                  onChange({ automation: typeof value.automation === "number" ? value.automation : undefined,
                    strategies: typeof value.strategies === "number" ? value.strategies : undefined });
                }
              } catch { /* A partial or malformed event cannot change UI state. */ }
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch { /* The existing timed refresh remains active until reconnect. */ }
      if (!stopped) retry = setTimeout(() => { void connect(); }, 5_000);
    };
    void connect();
    return () => { stopped = true; controller.abort(); if (retry) clearTimeout(retry); };
  }, [onChange]);
  return null;
}

export function RealtimeSignalsProvider({ children }: { children: ReactNode }) {
  const [revisions, setRevisions] = useState<Revisions>({});
  if (localStorageEnabled) return (
    <RealtimeContext.Provider value={revisions}>
      <LocalSignalBridge onChange={setRevisions} />
      {children}
    </RealtimeContext.Provider>
  );
  if (!client) return children;
  return (
    <RealtimeContext.Provider value={revisions}>
      <ConvexProviderWithAuth client={client} useAuth={useSupabaseAuth}>
        <SignalBridge onChange={setRevisions} />
        {children}
      </ConvexProviderWithAuth>
    </RealtimeContext.Provider>
  );
}

export function useRealtimeSignals() {
  return useContext(RealtimeContext);
}
