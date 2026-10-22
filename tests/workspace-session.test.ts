import { beforeEach, expect, test, vi } from "vitest";
const calls = vi.hoisted(() => ({ getSession: vi.fn(), refreshSession: vi.fn(), refreshReadSession: vi.fn(), signOut: vi.fn(), request: vi.fn() }));
vi.mock("@/lib/supabase/client", () => ({ getSupabaseBrowserClient: () => ({ auth: calls }) }));
vi.mock("@/lib/api", async original => ({ ...await original<typeof import("../lib/api")>(), requestJson: calls.request,
  refreshReadSession: calls.refreshReadSession }));
const { readWorkspaceSession, signOutThisDevice } = await import("../lib/workspace-session");
const previous = { kind: "ready", user: { id: "a", userType: "owner" },
  account: { id: "exchange-a", environment: "production" } } satisfies import("../lib/workspace-session").WorkspaceSession;
const authSession = (id = "a") => ({ user: { id, email: `${id}@example.test`, user_metadata: {} } });

beforeEach(() => {
  vi.resetAllMocks();
  calls.getSession.mockResolvedValue({ data: { session: authSession() }, error: null });
  calls.request.mockResolvedValue({ success: true, authenticated: true, connected: true, user: { id: "a" }, account: previous.account });
});

test("a backend outage preserves this user's identity and last known Delta link", async () => {
  calls.request.mockRejectedValue(new Error("Network unavailable"));
  expect(await readWorkspaceSession(previous)).toMatchObject({ kind: "unavailable", user: { id: "a", userType: "owner" }, account: previous.account });
});

test("a different user cannot inherit the previous user's connection during an outage", async () => {
  calls.getSession.mockResolvedValue({ data: { session: authSession("b") }, error: null });
  calls.request.mockRejectedValue(new Error("Unavailable"));
  expect(await readWorkspaceSession(previous)).toMatchObject({ kind: "unavailable", user: { id: "b" }, account: null });
});

test("a genuinely absent session clears all private workspace state", async () => {
  calls.getSession.mockResolvedValue({ data: { session: null }, error: null });
  expect(await readWorkspaceSession(previous)).toEqual({ kind: "signed-out" });
  expect(calls.request).not.toHaveBeenCalled();
});

test("a transient auth-network error retains the prior workspace instead of signing out", async () => {
  calls.getSession.mockResolvedValue({ data: { session: null }, error: { name: "AuthRetryableFetchError", status: 503 } });
  expect(await readWorkspaceSession(previous)).toMatchObject({ kind: "unavailable", user: previous.user, account: previous.account });
});

test("a terminal SDK session error clears the workspace even without an auth event", async () => {
  calls.getSession.mockResolvedValue({ data: { session: null }, error: { name: "AuthApiError", status: 400 } });
  expect(await readWorkspaceSession(previous)).toEqual({ kind: "signed-out" });
});

test("an old backend's unauthenticated HTTP 200 response is rechecked after refresh", async () => {
  calls.request.mockResolvedValueOnce({ success: true, authenticated: false, connected: false, user: null });
  calls.refreshReadSession.mockResolvedValue(authSession());
  expect(await readWorkspaceSession(previous)).toMatchObject({ kind: "ready", account: previous.account });
  expect(calls.refreshReadSession).toHaveBeenCalledTimes(1);
});

test("an explicitly disconnected Delta account keeps Google signed in", async () => {
  calls.request.mockResolvedValue({ success: true, authenticated: true, connected: false, user: { id: "a" }, account: null });
  expect(await readWorkspaceSession(previous)).toMatchObject({ kind: "ready", user: { id: "a" }, account: null });
  expect(calls.signOut).not.toHaveBeenCalled();
});

test("a backend identity mismatch never replaces the authenticated user", async () => {
  calls.request.mockResolvedValue({ success: true, authenticated: true, connected: true, user: { id: "b" }, account: { id: "exchange-b" } });
  expect(await readWorkspaceSession(previous)).toMatchObject({ kind: "unavailable", user: { id: "a" }, account: previous.account });
});

test("ordinary sign-out uses local scope and surfaces SDK errors", async () => {
  calls.signOut.mockResolvedValueOnce({ error: null }).mockResolvedValueOnce({ error: new Error("Sign-out failed") });
  await signOutThisDevice();
  expect(calls.signOut).toHaveBeenCalledWith({ scope: "local" });
  await expect(signOutThisDevice()).rejects.toThrow("Sign-out failed");
});
