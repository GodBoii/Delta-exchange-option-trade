import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Session } from "@supabase/supabase-js";

const auth = vi.hoisted(() => ({ getSession: vi.fn(), refreshSession: vi.fn() }));
vi.mock("@/lib/supabase/client", () => ({ getSupabaseBrowserClient: () => ({ auth }) }));
const { ApiError, requestJson, resetApiOrigin, SESSION_AUTH_EVENT } = await import("../lib/api");
const session = (id: string, token: string): Session => ({
  access_token: token, refresh_token: "private-refresh", expires_in: 3600, token_type: "bearer",
  user: { id, app_metadata: {}, user_metadata: {}, aud: "authenticated", created_at: "2026-10-22" },
});
const old = session("a", "old"), fresh = session("a", "new");
const rejection = (code = "not_authenticated") => Response.json({ error: { code, message: "Rejected" } }, { status: 401 });
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  vi.clearAllMocks();
  resetApiOrigin();
  vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.test");
  vi.stubGlobal("window", Object.assign(new EventTarget(), { location: { hostname: "site.test", protocol: "https:" } }));
  auth.getSession.mockResolvedValue({ data: { session: old }, error: null });
  auth.refreshSession.mockResolvedValue({ data: { session: fresh }, error: null });
  fetcher = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetcher);
  fetcher.mockImplementation(async url => String(url).endsWith("/health")
    ? Response.json({ success: true, service: "delta-strategy-api" }) : rejection());
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test("an authenticated read refreshes once and retries with the new token", async () => {
  fetcher.mockImplementation(async (url, init) => String(url).endsWith("/health")
    ? Response.json({ success: true, service: "delta-strategy-api" })
    : new Headers(init?.headers).get("Authorization") === "Bearer new" ? Response.json({ ok: true }) : rejection());
  expect(await requestJson("/api/session")).toEqual({ ok: true });
  expect(auth.refreshSession).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/api/session"))).toHaveLength(2);
});

test.each(["POST", "PUT", "DELETE"])("%s is never automatically replayed", async method => {
  await expect(requestJson("/api/orders", { method, body: "{}" })).rejects.toMatchObject({ status: 401, code: "not_authenticated" });
  expect(auth.refreshSession).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/api/orders"))).toHaveLength(1);
});

test("a Delta credential rejection does not refresh or terminate application auth", async () => {
  const events = vi.fn();
  window.addEventListener(SESSION_AUTH_EVENT, events);
  fetcher.mockImplementation(async url => String(url).endsWith("/health")
    ? Response.json({ success: true, service: "delta-strategy-api" }) : rejection("delta_not_connected"));
  await expect(requestJson("/api/account/overview")).rejects.toBeInstanceOf(ApiError);
  expect(auth.refreshSession).not.toHaveBeenCalled();
  expect(events).not.toHaveBeenCalled();
});

test("a read for a previous user never retries with a different user's token", async () => {
  auth.getSession.mockResolvedValueOnce({ data: { session: old }, error: null })
    .mockResolvedValue({ data: { session: session("b", "other-user") }, error: null });
  await expect(requestJson("/api/session")).rejects.toMatchObject({ code: "not_authenticated" });
  expect(auth.refreshSession).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/api/session"))).toHaveLength(1);
});

test("a temporary refresh failure keeps its service error classification", async () => {
  auth.refreshSession.mockResolvedValue({ data: { session: null }, error: { name: "AuthRetryableFetchError", status: 503 } });
  await expect(requestJson("/api/session")).rejects.toMatchObject({ status: 503, code: "auth_unavailable" });
});

test("simultaneous expired reads share a single refresh", async () => {
  let complete: ((value: { data: { session: Session }; error: null }) => void) | undefined;
  const refreshing = new Promise(resolve => { complete = resolve; });
  auth.refreshSession.mockReturnValue(refreshing);
  const first = requestJson("/api/first").catch(error => error);
  const second = requestJson("/api/second").catch(error => error);
  await vi.waitFor(() => expect(auth.refreshSession).toHaveBeenCalledTimes(1));
  complete?.({ data: { session: fresh }, error: null });
  await Promise.all([first, second]);
  expect(auth.refreshSession).toHaveBeenCalledTimes(1);
});

test("repeated backend rejections cannot create a refresh loop", async () => {
  await expect(requestJson("/api/session")).rejects.toMatchObject({ code: "not_authenticated" });
  await expect(requestJson("/api/session")).rejects.toMatchObject({ code: "not_authenticated" });
  expect(auth.refreshSession).toHaveBeenCalledTimes(1);
});
