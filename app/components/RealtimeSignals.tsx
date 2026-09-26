"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { apiOrigin } from "@/lib/api";

type Revisions = { automation?: number; strategies?: number };
const RealtimeContext = createContext<Revisions>({});

/** Backend revision stream: each change event tells subscribed views to refetch. */
function RevisionStream({ onChange }: { onChange: (value: Revisions) => void }) {
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
  return (
    <RealtimeContext.Provider value={revisions}>
      <RevisionStream onChange={setRevisions} />
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtimeSignals() {
  return useContext(RealtimeContext);
}
