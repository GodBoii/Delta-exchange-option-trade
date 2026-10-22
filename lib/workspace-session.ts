import type { Session } from "@supabase/supabase-js";
import type { Account, AppUser, SessionResponse } from "@/lib/app-types";
import { ApiError, authFailure, refreshReadSession, requestJson } from "@/lib/api";
import { resolveProfileAvatar } from "@/lib/profile-avatar";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export type WorkspaceSession =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "ready"; user: AppUser; account: Account | null }
  | { kind: "unavailable"; user: AppUser | null; account: Account | null };

function identity(session: Session): AppUser {
  const metadata = session.user.user_metadata;
  return {
    id: session.user.id, email: session.user.email,
    displayName: String(metadata.full_name ?? metadata.name ?? session.user.email?.split("@")[0] ?? "Client"),
    avatarUrl: resolveProfileAvatar(metadata.avatar_url, metadata.picture),
  };
}

/** A failed check retains only this user's last known connection, never another user's data. */
export async function readWorkspaceSession(previous: WorkspaceSession, signal?: AbortSignal): Promise<WorkspaceSession> {
  const old = previous.kind === "ready" || previous.kind === "unavailable" ? previous : null;
  let user: AppUser | null = old?.user ?? null;
  let account: Account | null = old?.account ?? null;
  try {
    const auth = getSupabaseBrowserClient().auth;
    const initial = await auth.getSession();
    if (initial.error) throw authFailure(initial.error);
    if (!initial.data.session) return { kind: "signed-out" };
    const authUser = identity(initial.data.session);
    user = old?.user?.id === authUser.id ? { ...old.user, ...authUser } : authUser;
    account = old?.user?.id === authUser.id ? old.account : null;
    let data = await requestJson<SessionResponse>("/api/session", { signal: signal ?? AbortSignal.timeout(12_000) });
    // Older backends return HTTP 200 when an access token is rejected.
    if (!data.authenticated) {
      const refreshed = await refreshReadSession(initial.data.session);
      if (!refreshed) {
        const current = await auth.getSession();
        return !current.error && !current.data.session ? { kind: "signed-out" } : { kind: "unavailable", user, account };
      }
      if (refreshed.user.id !== authUser.id) return { kind: "unavailable", user: null, account: null };
      data = await requestJson<SessionResponse>("/api/session", { signal });
    }
    if (!data.success || !data.authenticated || data.user?.id !== authUser.id) {
      return { kind: "unavailable", user, account };
    }
    return {
      kind: "ready", account: data.connected ? data.account : null,
      user: { ...data.user, avatarUrl: resolveProfileAvatar(authUser.avatarUrl, data.user.avatarUrl) },
    };
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof ApiError && error.code === "not_authenticated") {
      const current = await getSupabaseBrowserClient().auth.getSession();
      if (!current.error && !current.data.session) return { kind: "signed-out" };
      if (current.error && authFailure(current.error).status === 401) return { kind: "signed-out" };
    }
    return { kind: "unavailable", user, account };
  }
}

export async function signOutThisDevice() {
  const { error } = await getSupabaseBrowserClient().auth.signOut({ scope: "local" });
  if (error) throw error;
}
