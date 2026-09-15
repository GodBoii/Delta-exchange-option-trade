/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const overview = makeFunctionReference<"query">("accounts:overview");
const credentials = makeFunctionReference<"query">("accounts:credentials");
const save = makeFunctionReference<"mutation">("accounts:saveConnection");
const revoke = makeFunctionReference<"mutation">("accounts:revoke");
beforeEach(() => { vi.stubEnv("CONVEX_TRADING_SECRET", "trading"); vi.stubEnv("CONVEX_RESEARCH_SECRET", "research"); });
afterEach(() => vi.unstubAllEnvs());

test("research can read account metadata but cannot access encrypted credentials", async () => {
  const t = convexTest(schema, modules);
  await t.mutation(save, { secret: "trading", expectedFingerprint: null, value: {
    id: "connection", user_id: "owner", delta_user_id: "account", account_name: "Main", email_masked: null,
    environment: "production", status: "connected", ciphertext: "encrypted", fingerprint: "fingerprint", updated_at: "date",
  } });
  const result = await t.query(overview, { secret: "research", userId: "owner" });
  expect(result.connection.status).toBe("connected");
  expect(result.connection.ciphertext).toBeUndefined();
  await expect(t.query(credentials, { secret: "research", userId: "owner" })).rejects.toThrow("Unauthorized");
  await expect(t.mutation(revoke, { secret: "research", userId: "owner" })).rejects.toThrow("Unauthorized");
  await t.mutation(revoke, { secret: "trading", userId: "owner" });
  expect((await t.query(credentials, { secret: "trading", userId: "owner" })).ciphertext).toBeNull();
});
