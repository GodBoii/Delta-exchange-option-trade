"use client";

import { useEffect, useState } from "react";
import { apiOrigin } from "@/lib/api";
import type { DeltaRecord } from "@/lib/app-types";
import { NO_PRICES, parseStreamMessage, type LivePrices } from "@/lib/portfolio";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Live account feed from the trading API's `/ws/portfolio` stream.
 *
 * - `live`: positions, orders and wallet rows come from Delta's private socket.
 * - `syncing`: connected, but the account snapshot is not ready (Delta socket
 *   reconnecting); prices may still flow and the caller keeps its REST data.
 * - `reconnecting` / `connecting`: no stream right now.
 * - `unavailable`: the API has live events switched off; REST polling only.
 */
export type StreamFeed = "connecting" | "live" | "syncing" | "reconnecting" | "unavailable";

export type LiveAccount = {
  positions: DeltaRecord[];
  orders: DeltaRecord[];
  balances: DeltaRecord[];
  receivedAt: number;
};

type StreamState = {
  feed: StreamFeed;
  account: LiveAccount | null;
  prices: LivePrices;
};

const INITIAL: StreamState = { feed: "connecting", account: null, prices: NO_PRICES };
const MAX_BACKOFF_MS = 10_000;
// The API is up but the feature is off or the account is not linked: no point
// hammering it, the REST view keeps working meanwhile.
const UNAVAILABLE_RETRY_MS = 5 * 60_000;
const NOT_CONNECTED_RETRY_MS = 60_000;

const CLOSE_NOT_CONNECTED = 4403;
const CLOSE_UNAVAILABLE = 4503;
const CLOSE_SESSION_REPLACED = 4001;

async function accessToken() {
  const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
  return session?.access_token ?? null;
}

export function usePortfolioStream(enabled: boolean): StreamState {
  const [state, setState] = useState<StreamState>(INITIAL);

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let stopped = false;
    let attempt = 0;

    const schedule = (delay: number) => {
      if (stopped) return;
      reconnectTimer = window.setTimeout(() => void connect(), delay);
    };

    const connect = async () => {
      if (stopped) return;
      let url: string;
      try {
        url = `${(await apiOrigin()).replace(/^http/, "ws")}/ws/portfolio`;
      } catch {
        setState(current => ({ ...current, feed: "reconnecting", account: null }));
        attempt += 1;
        schedule(Math.min(MAX_BACKOFF_MS, 500 * 2 ** Math.min(attempt, 5)));
        return;
      }
      if (stopped) return;
      const next = new WebSocket(url);
      socket = next;

      next.onopen = () => {
        // The token travels in the first frame, not the URL, so it never lands in proxy logs.
        void accessToken().then(token => {
          if (next.readyState !== WebSocket.OPEN) return;
          if (!token) { next.close(); return; }
          next.send(JSON.stringify({ type: "auth", token }));
        });
      };

      next.onmessage = event => {
        if (typeof event.data !== "string") return;
        const message = parseStreamMessage(event.data);
        if (!message) return;
        attempt = 0;
        switch (message.type) {
          case "status":
            setState(current => message.private === "live"
              ? { ...current, feed: "live" }
              : { ...current, feed: "syncing", account: null });
            break;
          case "state":
            setState(current => ({
              ...current,
              feed: "live",
              account: {
                positions: message.positions,
                orders: message.orders,
                balances: message.balances,
                receivedAt: message.at
              }
            }));
            break;
          case "prices":
            setState(current => ({ ...current, prices: { marks: message.marks, indices: message.indices } }));
            break;
          case "ping":
            break;
          default: {
            const exhaustive: never = message;
            return exhaustive;
          }
        }
      };

      next.onclose = event => {
        if (socket !== next) return;
        socket = null;
        if (stopped) return;
        if (event.code === CLOSE_UNAVAILABLE) {
          setState({ feed: "unavailable", account: null, prices: NO_PRICES });
          schedule(UNAVAILABLE_RETRY_MS);
          return;
        }
        setState(current => ({ ...current, feed: "reconnecting", account: null }));
        if (event.code === CLOSE_SESSION_REPLACED) {
          attempt = 0;
          schedule(250);
          return;
        }
        if (event.code === CLOSE_NOT_CONNECTED) {
          schedule(NOT_CONNECTED_RETRY_MS);
          return;
        }
        attempt += 1;
        schedule(Math.min(MAX_BACKOFF_MS, 500 * 2 ** Math.min(attempt, 5)));
      };
    };

    // A refreshed session token is handed to the open stream so the server's
    // periodic re-check keeps passing without a reconnect.
    const { data: subscription } = getSupabaseBrowserClient().auth.onAuthStateChange((event, session) => {
      if (event !== "TOKEN_REFRESHED" && event !== "SIGNED_IN") return;
      if (session?.access_token && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "auth", token: session.access_token }));
      }
    });

    void connect();
    return () => {
      stopped = true;
      subscription.subscription.unsubscribe();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
      socket = null;
      setState(INITIAL);
    };
  }, [enabled]);

  return state;
}
