/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const begin = makeFunctionReference<"mutation">("orderIntents:begin");
const resolve = makeFunctionReference<"mutation">("orderIntents:resolve");
const unresolved = makeFunctionReference<"query">("orderIntents:unresolved");
const claimProducts = makeFunctionReference<"mutation">("orderIntents:claimProducts");
const releaseProducts = makeFunctionReference<"mutation">("orderIntents:releaseProducts");
const ingest = makeFunctionReference<"mutation">("exchangeFills:ingest");
const forProduct = makeFunctionReference<"query">("exchangeFills:forProduct");
const input = { secret: "test-secret", accountId: "account-a", clientOrderId: "order-a", payload: "{}" };

beforeEach(() => vi.stubEnv("CONVEX_TRADING_SECRET", input.secret));
afterEach(() => vi.unstubAllEnvs());

test("one identity permits one dispatch and rejects changed payloads", async () => {
  const t = convexTest(schema, modules);
  expect(await t.mutation(begin, input)).toMatchObject({ dispatch: true });
  expect(await t.mutation(begin, input)).toMatchObject({ dispatch: false, outcome: { kind: "unknown" } });
  await expect(t.mutation(begin, { ...input, payload: '{"size":2}' })).rejects.toThrow("different payload");
});

test("resolved outcomes remain stable and leave the recovery queue", async () => {
  const t = convexTest(schema, modules);
  await t.mutation(begin, input);
  const outcome = { kind: "accepted", response: '{"result":{"id":1}}' };
  const identity = { secret: input.secret, accountId: input.accountId, clientOrderId: input.clientOrderId };
  await t.mutation(resolve, { ...identity, outcome });
  await t.mutation(resolve, { ...identity, outcome });
  await t.mutation(resolve, { ...identity, outcome: { kind: "unknown" } });
  expect(await t.mutation(begin, input)).toMatchObject({ dispatch: false, outcome });
  const result = await t.query(unresolved, {
    secret: input.secret, accountId: input.accountId, paginationOpts: { numItems: 20, cursor: null },
  });
  expect(result.page).toEqual([]);
  await expect(t.mutation(resolve, {
    ...identity, outcome: { kind: "rejected", code: "bad", message: "bad" },
  })).rejects.toThrow("Conflicting");
});

test("account identities are isolated and browser credentials confer no write access", async () => {
  const t = convexTest(schema, modules);
  await expect(t.withIdentity({ subject: "account-a" }).mutation(begin, {
    ...input, secret: "incorrect",
  })).rejects.toThrow("Unauthorized");
  await t.mutation(begin, input);
  expect(await t.mutation(begin, { ...input, accountId: "account-b" })).toMatchObject({ dispatch: true });
});

test("product ownership is exclusive and a failed multi-product claim is atomic", async () => {
  const t = convexTest(schema, modules);
  const account = { secret: input.secret, accountId: input.accountId };
  await t.mutation(claimProducts, { ...account, strategyId: "first", productIds: ["2"] });
  await expect(t.mutation(claimProducts, { ...account, strategyId: "second", productIds: ["1", "2"] }))
    .rejects.toThrow("already owned");
  await t.mutation(claimProducts, { ...account, strategyId: "third", productIds: ["1"] });
  await t.mutation(releaseProducts, { ...account, strategyId: "first" });
  await t.mutation(claimProducts, { ...account, strategyId: "second", productIds: ["2"] });
});

test("duplicate fill delivery preserves quantity and late fees update once", async () => {
  const t = convexTest(schema, modules);
  const account = { secret: input.secret, accountId: input.accountId };
  const fill = { fillId: "fill-1", productId: "1", orderId: "order-1", side: "buy",
    quantity: "11", price: "439", commission: null, occurredAt: "2026-09-13T08:39:39.000000+00:00" };
  await t.mutation(ingest, { ...account, fills: [fill, fill] });
  await t.mutation(ingest, { ...account, fills: [{ ...fill, commission: "0.01" }] });
  await t.mutation(ingest, { ...account, fills: [fill] });
  const result = await t.query(forProduct, {
    ...account, productId: "1", from: "2026-09-13", paginationOpts: { numItems: 20, cursor: null },
  });
  expect(result.page).toHaveLength(1);
  expect(result.page[0]).toMatchObject({ quantity: "11", commission: "0.01" });
  await expect(t.mutation(ingest, { ...account, fills: [{ ...fill, price: "440" }] }))
    .rejects.toThrow("Conflicting fill facts");
});
