/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const applyBatch = makeFunctionReference<"mutation">("recovery:applyBatch");
const readOnly = makeFunctionReference<"query">("recovery:readOnlyForUser");
const prune = makeFunctionReference<"mutation">("recovery:prunePage");
const secret = "test-recovery-secret";

beforeEach(() => vi.stubEnv("CONVEX_TRADING_SECRET", secret));
afterEach(() => vi.unstubAllEnvs());

test("recovery batches are idempotent and public views exclude credentials", async () => {
  const t = convexTest(schema, modules);
  const strategy = { id: "s1", user_id: "alice", name: "Test", status: "scheduled",
    entry_at: "2026-09-25T00:00:00Z", created_at: "2026-09-24T00:00:00Z" };
  const items = [
    { entityType: "strategies", entityKey: "s1", revision: 1, operation: "upsert",
      payloadJson: JSON.stringify({ owner_id: "alice", data: strategy }) },
    { entityType: "users", entityKey: "alice", revision: 1, operation: "upsert",
      payloadJson: JSON.stringify({ user_id: "alice", connection: { ciphertext: "secret" } }) },
  ];
  await t.mutation(applyBatch, { secret, items });
  await t.mutation(applyBatch, { secret, items });
  expect(await t.withIdentity({ subject: "alice" }).query(readOnly, { entityType: "strategies" }))
    .toMatchObject([{ summary: { id: "s1", status: "scheduled" } }]);
  expect(await t.withIdentity({ subject: "bob" }).query(readOnly, { entityType: "strategies" })).toEqual([]);
  expect(await t.withIdentity({ subject: "alice" }).query(readOnly, { entityType: "saved_strategies" })).toEqual([]);
  await expect(t.withIdentity({ subject: "alice" }).mutation(applyBatch, { secret: "wrong", items }))
    .rejects.toThrow("Unauthorized");
  expect(await t.mutation(prune, { secret, before: Date.now() + 1 })).toBe(0);
  expect(await t.withIdentity({ subject: "alice" }).query(readOnly, { entityType: "strategies" }))
    .toHaveLength(1);
});

test("newer revision replaces an older record and deletion clears its public view", async () => {
  const t = convexTest(schema, modules);
  const base = { entityType: "strategies", entityKey: "s2", operation: "upsert",
    payloadJson: JSON.stringify({ owner_id: "alice", data: { id: "s2", user_id: "alice", status: "scheduled" } }) };
  await t.mutation(applyBatch, { secret, items: [{ ...base, revision: 1 }] });
  await t.mutation(applyBatch, { secret, items: [{ ...base, revision: 3, operation: "delete" }] });
  await t.mutation(applyBatch, { secret, items: [{ ...base, revision: 2 }] });
  expect(await t.withIdentity({ subject: "alice" }).query(readOnly, { entityType: "strategies" })).toEqual([]);
  expect(await t.mutation(prune, { secret, before: Date.now() + 1 })).toBeGreaterThanOrEqual(1);
});

test("accepted order intents stay recoverable until the strategy is closed", async () => {
  const t = convexTest(schema, modules);
  const item = { entityType: "order_intents", entityKey: "india:1:order1", revision: 1,
    operation: "upsert", payloadJson: JSON.stringify({
      account_id: "india:1", client_order_id: "order1", unresolved: false,
      outcome: { kind: "accepted", response: "{}" }, recovery_closed_at: null,
    }) };
  await t.mutation(applyBatch, { secret, items: [item] });
  expect(await t.mutation(prune, { secret, before: Date.now() + 1 })).toBe(0);
  await t.mutation(applyBatch, { secret, items: [{ ...item, revision: 2,
    payloadJson: JSON.stringify({ ...JSON.parse(item.payloadJson), recovery_closed_at: "2026-01-01T00:00:00Z" }),
  }] });
  expect(await t.mutation(prune, { secret, before: Date.now() })).toBe(1);
});
