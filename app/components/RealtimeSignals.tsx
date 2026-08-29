"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ConvexProviderWithAuth, ConvexReactClient, useConvexAuth, useQuery } from "convex/react";
import { api } from "@/convex/_generated/api";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

type Revisions = { automation?: number; strategies?: number };
const RealtimeContext = createContext<Revisions>({});
const url = process.env.NEXT_PUBLIC_CONVEX_URL;
const client = url ? new ConvexReactClient(url) : null;

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

export function RealtimeSignalsProvider({ children }: { children: ReactNode }) {
  const [revisions, setRevisions] = useState<Revisions>({});
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
