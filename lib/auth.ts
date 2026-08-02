import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { db, type AccountRow } from "@/lib/db";
import { decryptSecret, encryptSecret, opaqueToken, tokenHash } from "@/lib/crypto";
import type { DeltaEnvironment } from "@/lib/config";
import { DeltaClient, type DeltaProfile } from "@/lib/delta";
import { AppError } from "@/lib/http";

const COOKIE_NAME = "delta_desk_session";
const SESSION_DAYS = 30;

function maskEmail(email?: string) {
  if (!email || !email.includes("@")) return null;
  const [name, domain] = email.split("@");
  return `${name.slice(0, 2)}${"*".repeat(Math.max(2, name.length - 2))}@${domain}`;
}

export function credentialsFor(account: AccountRow) {
  return {
    apiKey: account.api_key,
    apiSecret: decryptSecret({ ciphertext: account.secret_ciphertext, iv: account.secret_iv, tag: account.secret_tag }),
    environment: account.environment
  };
}

export async function createConnection(apiKey: string, apiSecret: string, environment: DeltaEnvironment) {
  const client = new DeltaClient({ apiKey, apiSecret, environment });
  const profile = (await client.profile()).result;
  const now = new Date().toISOString();
  const encrypted = encryptSecret(apiSecret);
  const existing = db.prepare("SELECT id FROM accounts WHERE api_key = ?").get(apiKey) as { id: string } | undefined;
  const accountId = existing?.id ?? crypto.randomUUID();
  db.prepare(`
    INSERT INTO accounts (id, api_key, secret_ciphertext, secret_iv, secret_tag, environment, delta_user_id, account_name, email_masked, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(api_key) DO UPDATE SET secret_ciphertext=excluded.secret_ciphertext, secret_iv=excluded.secret_iv,
      secret_tag=excluded.secret_tag, environment=excluded.environment, delta_user_id=excluded.delta_user_id,
      account_name=excluded.account_name, email_masked=excluded.email_masked, updated_at=excluded.updated_at
  `).run(accountId, apiKey, encrypted.ciphertext, encrypted.iv, encrypted.tag, environment, String(profile.id), profile.account_name ?? "Main", maskEmail(profile.email), now, now);
  const token = opaqueToken();
  const expiresAt = new Date(Date.now() + SESSION_DAYS * 86400_000);
  db.prepare("INSERT INTO sessions (token_hash, account_id, expires_at, created_at) VALUES (?, ?, ?, ?)")
    .run(tokenHash(token), accountId, expiresAt.toISOString(), now);
  return { token, expiresAt, account: publicAccount({ ...profile, environment }) };
}

function publicAccount(profile: DeltaProfile & { environment?: DeltaEnvironment }) {
  return {
    id: String(profile.id),
    accountName: profile.account_name ?? "Main",
    email: maskEmail(profile.email),
    marginMode: profile.margin_mode ?? null,
    environment: profile.environment ?? "production"
  };
}

export function setSessionCookie(response: NextResponse, token: string, expiresAt: Date) {
  response.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    expires: expiresAt
  });
}

export async function currentAccount(required = true): Promise<AccountRow | null> {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  if (!token) {
    if (required) throw new AppError(401, "Connect your Delta account to continue", "not_connected");
    return null;
  }
  const row = db.prepare(`
    SELECT a.* FROM sessions s JOIN accounts a ON a.id = s.account_id
    WHERE s.token_hash = ? AND s.expires_at > ?
  `).get(tokenHash(token), new Date().toISOString()) as AccountRow | undefined;
  if (!row && required) throw new AppError(401, "Your session has expired", "session_expired");
  return row ?? null;
}

export async function currentClient() {
  const account = await currentAccount(true);
  return { account: account!, client: new DeltaClient(credentialsFor(account!)) };
}

export async function destroyCurrentSession(response: NextResponse) {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  if (token) db.prepare("DELETE FROM sessions WHERE token_hash = ?").run(tokenHash(token));
  response.cookies.set(COOKIE_NAME, "", { httpOnly: true, sameSite: "strict", path: "/", maxAge: 0 });
}
