import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Browser client for the private FastAPI trading backend.
 *
 * The backend is not guaranteed to sit on a fixed port during local
 * development, so the origin is discovered once per session by probing the
 * configured candidates in parallel and keeping the first service that
 * identifies itself as the Delta strategy API.
 */

export type ApiRequester = <T>(url: string, init?: RequestInit) => Promise<T>;

const DEFAULT_PORTS = "8000,8585,8085,8011,8001";
const PROBE_TIMEOUT_MS = 1_200;
const REQUEST_TIMEOUT_MS = 8_000;

let resolvedOrigin: string | null = null;
let pendingResolution: Promise<string> | null = null;

/** Called after sign-in so a restarted backend is rediscovered. */
export function resetApiOrigin() {
  resolvedOrigin = null;
  pendingResolution = null;
}

function candidateOrigins() {
  const explicit = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");
  const ports = (process.env.NEXT_PUBLIC_API_PORTS ?? DEFAULT_PORTS)
    .split(",")
    .map(port => port.trim())
    .filter(port => /^\d{2,5}$/.test(port));
  const host = ["localhost", "127.0.0.1"].includes(window.location.hostname) ? window.location.hostname : "localhost";
  return Array.from(new Set([
    ...(explicit ? [explicit] : []),
    ...ports.map(port => `http://${host}:${port}`)
  ])).filter(origin => window.location.protocol !== "https:" || origin.startsWith("https://"));
}

async function discoverApiOrigin() {
  const candidates = candidateOrigins();
  if (!candidates.length) throw new Error("The trading backend is not configured for this website.");

  const probes = candidates.map(async origin => {
    const response = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(PROBE_TIMEOUT_MS), cache: "no-store" });
    if (!response.ok) throw new Error(`${origin} is unavailable`);
    const health = await response.json().catch(() => null) as { success?: boolean; service?: string } | null;
    if (!health?.success || health.service !== "delta-strategy-api") throw new Error(`${origin} is not the trading API`);
    return origin;
  });

  try {
    return await Promise.any(probes);
  } catch {
    throw new Error("No local trading backend was found on the configured ports.");
  }
}

export async function apiOrigin() {
  if (resolvedOrigin) return resolvedOrigin;
  pendingResolution ??= discoverApiOrigin();
  try {
    resolvedOrigin = await pendingResolution;
    return resolvedOrigin;
  } finally {
    pendingResolution = null;
  }
}

/**
 * Authenticated JSON request. The Supabase access token is attached per call so
 * the backend can verify the caller; no Delta secret ever reaches the browser.
 *
 * Pass `signal: null` for long-running agent work that must not be aborted by
 * the default timeout.
 */
export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const origin = await apiOrigin();
  const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
  const signal = init && Object.prototype.hasOwnProperty.call(init, "signal")
    ? init.signal
    : AbortSignal.timeout(REQUEST_TIMEOUT_MS);

  const response = await fetch(`${origin}${url}`, {
    ...init,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
      ...init?.headers
    }
  });

  const data = await response.json().catch(() => ({})) as T & {
    message?: string;
    error?: string | { message?: string; code?: string };
  };

  if (!response.ok) {
    const nested = typeof data.error === "object" ? data.error?.message : data.error;
    throw new Error(data.message || nested || `Request failed (${response.status})`);
  }
  return data;
}
