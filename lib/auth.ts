import type { User } from "@supabase/supabase-js";
import { DeltaClient, type DeltaProfile } from "@/lib/delta";
import { AppError } from "@/lib/http";
import { getAuthenticatedUser } from "@/lib/supabase/server";
import { getSupabaseAdmin } from "@/lib/supabase/admin";

export type ConnectedAccount = {
  id: string;
  connection_id: string | null;
  delta_user_id: string | null;
  account_name: string | null;
  email_masked: string | null;
  environment: "production";
  status: string | null;
  app_email: string | null;
  display_name: string | null;
  avatar_url: string | null;
};

type CredentialRow = {
  connection_id: string;
  api_key: string;
  api_secret: string;
  environment: "production";
  delta_user_id: string | null;
  account_name: string | null;
  email_masked: string | null;
  status: string;
};

function maskEmail(email?: string) {
  if (!email || !email.includes("@")) return null;
  const [name, domain] = email.split("@");
  return `${name.slice(0, 2)}${"*".repeat(Math.max(2, name.length - 2))}@${domain}`;
}

function userName(user: User) {
  return String(user.user_metadata.full_name ?? user.user_metadata.name ?? user.email?.split("@")[0] ?? "Client");
}

export async function createConnection(userId: string, apiKey: string, apiSecret: string) {
  const client = new DeltaClient({ apiKey, apiSecret, environment: "production" });
  const profile = (await client.profile()).result;
  const admin = getSupabaseAdmin();
  const { data, error } = await admin.rpc("store_delta_connection", {
    p_user_id: userId,
    p_api_key: apiKey,
    p_api_secret: apiSecret,
    p_delta_user_id: String(profile.id),
    p_account_name: profile.account_name ?? "Main",
    p_email_masked: maskEmail(profile.email)
  });
  if (error) throw new AppError(500, "Could not securely store the Delta connection", "connection_store_failed");
  return { connectionId: String(data), account: publicDeltaAccount(profile) };
}

function publicDeltaAccount(profile: DeltaProfile) {
  return {
    id: String(profile.id),
    accountName: profile.account_name ?? "Main",
    email: maskEmail(profile.email),
    environment: "production" as const
  };
}

export async function requireAppUser() {
  const user = await getAuthenticatedUser();
  if (!user) throw new AppError(401, "Sign in to continue", "not_authenticated");
  return user;
}

export async function currentAccount(required = true): Promise<ConnectedAccount | null> {
  const user = await getAuthenticatedUser();
  if (!user) {
    if (required) throw new AppError(401, "Sign in to continue", "not_authenticated");
    return null;
  }
  const admin = getSupabaseAdmin();
  const [{ data: connection, error }, { data: profile }] = await Promise.all([
    admin.from("exchange_connections").select("id,delta_user_id,account_name,email_masked,environment,status").eq("user_id", user.id).maybeSingle(),
    admin.from("profiles").select("display_name,avatar_url").eq("id", user.id).maybeSingle()
  ]);
  if (error) throw new AppError(500, "Could not load the Delta connection", "connection_lookup_failed");
  if (!connection && required) throw new AppError(401, "Connect Delta Exchange to continue", "delta_not_connected");
  return {
    id: user.id,
    connection_id: connection?.id ?? null,
    delta_user_id: connection?.delta_user_id ?? null,
    account_name: connection?.account_name ?? null,
    email_masked: connection?.email_masked ?? null,
    environment: "production",
    status: connection?.status ?? null,
    app_email: user.email ?? null,
    display_name: profile?.display_name ?? userName(user),
    avatar_url: profile?.avatar_url ?? (user.user_metadata.avatar_url as string | undefined) ?? null
  };
}

export async function credentialsForUser(userId: string) {
  const admin = getSupabaseAdmin();
  const { data, error } = await admin.rpc("get_delta_credentials", { p_user_id: userId });
  const row = (Array.isArray(data) ? data[0] : data) as CredentialRow | undefined;
  if (error || !row || row.status !== "connected") {
    throw new AppError(401, "Connect Delta Exchange to continue", "delta_not_connected");
  }
  return {
    apiKey: row.api_key,
    apiSecret: row.api_secret,
    environment: "production" as const
  };
}

export async function currentClient() {
  const account = await currentAccount(true);
  return { account: account!, client: new DeltaClient(await credentialsForUser(account!.id)) };
}

export async function disconnectDelta(userId: string) {
  const admin = getSupabaseAdmin();
  const { error } = await admin.rpc("delete_delta_connection", { p_user_id: userId });
  if (error) throw new AppError(500, "Could not disconnect Delta Exchange", "disconnect_failed");
}
