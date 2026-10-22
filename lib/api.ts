import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { Session } from "@supabase/supabase-js";

/**
 * Browser client for the private FastAPI trading backend.
 *
 * The backend is not guaranteed to sit on a fixed port during local
 * development, so the origin is discovered once per session by probing the
 * configured candidates in parallel and keeping the first service that
 * identifies itself as the Delta strategy API.
 */

export type ApiRequester = <T>(url: string, init?: RequestInit) => Promise<T>;

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) {
    super(message);
    this.name = "ApiError";
  }
}

export const SESSION_AUTH_EVENT = "trade-cognition-session-auth";
let pendingRefresh: { userId: string; promise: Promise<Session | null> } | null = null;
let lastForcedRefresh: { userId: string; at: number } | null = null;

export function authFailure(error: { status?: number; name?: string }) {
  return new ApiError("Your session could not be verified. Please retry.",
    error.name === "AuthRetryableFetchError" || (error.status ?? 0) >= 500 ? 503 : 401,
    error.name === "AuthRetryableFetchError" || (error.status ?? 0) >= 500 ? "auth_unavailable" : "not_authenticated");
}

function notifyAuthFailure(userId: string | null) {
  window.dispatchEvent(new CustomEvent(SESSION_AUTH_EVENT, { detail: { userId } }));
}

export async function refreshReadSession(original: Session): Promise<Session | null> {
  const auth = getSupabaseBrowserClient().auth;
  const current = await auth.getSession();
  if (current.error) throw authFailure(current.error);
  if (current.data.session?.user.id !== original.user.id) return null;
  if (current.data.session.access_token !== original.access_token) return current.data.session;
  if (pendingRefresh?.userId === original.user.id) return pendingRefresh.promise;
  if (lastForcedRefresh?.userId === original.user.id && Date.now() - lastForcedRefresh.at < 30_000) return null;
  lastForcedRefresh = { userId: original.user.id, at: Date.now() };
  const promise = auth.refreshSession().then(({ data, error }) => {
    if (error) throw authFailure(error);
    return data.session?.user.id === original.user.id ? data.session : null;
  });
  pendingRefresh = { userId: original.user.id, promise };
  try {
    return await promise;
  } finally {
    if (pendingRefresh?.promise === promise) pendingRefresh = null;
  }
}

const DEFAULT_PORTS = "8000,8585,8085,8011,8001";
const PROBE_TIMEOUT_MS = 12_000;
const REQUEST_TIMEOUT_MS = 12_000;

let resolvedOrigin: string | null = null;
let pendingResolution: Promise<string> | null = null;

/** Called after sign-in so a restarted backend is rediscovered. */
export function resetApiOrigin() {
  resolvedOrigin = null;
  pendingResolution = null;
  lastForcedRefresh = null;
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
  const explicit = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");

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
    if (explicit) throw new Error(`The configured trading backend at ${explicit} could not be reached.`);
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

/** Resolves a backend-relative path such as a signed chart link against the API origin. */
export async function backendUrl(path: string) {
  if (/^https?:\/\//.test(path)) return path;
  return `${await apiOrigin()}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * Authenticated JSON request. The Supabase access token is attached per call so
 * the backend can verify the caller; no Delta secret ever reaches the browser.
 *
 * Pass `signal: null` for long-running agent work that must not be aborted by
 * the default timeout.
 */
export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const auth = await getSupabaseBrowserClient().auth.getSession();
  if (auth.error) {
    const error = authFailure(auth.error);
    if (error.status === 401) notifyAuthFailure(null);
    throw error;
  }
  const session = auth.data.session;
  const origin = await apiOrigin();
  const signal = init && Object.prototype.hasOwnProperty.call(init, "signal")
    ? init.signal
    : AbortSignal.timeout(REQUEST_TIMEOUT_MS);

  const send = async (token: string | undefined) => {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${origin}${url}`, { ...init, signal, headers, cache: "no-store" });
    const data: unknown = await response.json().catch(() => null);
    return { response, data };
  };
  let { response, data } = await send(session?.access_token);
  const failure = (payload: unknown, status: number) => {
    const body = payload && typeof payload === "object" ? payload : {};
    const error = "error" in body ? body.error : null;
    const nested = error && typeof error === "object" ? error : {};
    const message = "message" in body && typeof body.message === "string" ? body.message
      : typeof error === "string" ? error
        : "message" in nested && typeof nested.message === "string" ? nested.message : `Request failed (${status})`;
    const code = "code" in nested && typeof nested.code === "string" ? nested.code : "request_failed";
    return new ApiError(message, status, code);
  };
  let error = failure(data, response.status);
  // Only an application-auth rejection is recoverable here. Writes are never replayed.
  if (response.status === 401 && error.code === "not_authenticated" && session
    && (init?.method ?? "GET").toUpperCase() === "GET" && !new Headers(init?.headers).has("Authorization")) {
    try {
      const refreshed = await refreshReadSession(session);
      if (refreshed && !signal?.aborted) ({ response, data } = await send(refreshed.access_token));
    } catch (refreshError) {
      if (refreshError instanceof ApiError && refreshError.status === 401) notifyAuthFailure(session.user.id);
      throw refreshError;
    }
    error = failure(data, response.status);
  }
  if (!response.ok) {
    if (error.status === 401 && error.code === "not_authenticated") notifyAuthFailure(session?.user.id ?? null);
    throw error;
  }
  return data as T;
}

/** Best-effort diagnostics use only fixed event names and a verified caller on the server. */
export function reportSessionEvent(event: "signed_in" | "token_refreshed" | "sign_out_local", token: string) {
  void requestJson("/api/session/events", {
    method: "POST", headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ event }), signal: AbortSignal.timeout(2_000),
  }).catch(() => { /* An unavailable diagnostic endpoint must not block authentication. */ });
}
