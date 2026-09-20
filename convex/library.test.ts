/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const save = makeFunctionReference<"mutation">("library:save");
const remove = makeFunctionReference<"mutation">("library:remove");
const list = makeFunctionReference<"query">("library:list");
const get = makeFunctionReference<"query">("library:serverGet");
const updateDefault = makeFunctionReference<"mutation">("library:serverUpdateDefault");
const importRows = makeFunctionReference<"mutation">("library:importStrategies");
const id = "11111111-1111-4111-8111-111111111111";
const definition = JSON.stringify({ name: "Test strategy", enabledForAi: true, legs: [{ id: "call" }] });
const draft = { id, name: "Test strategy", definitionJson: definition, enabled: true, expectedVersion: null };
beforeEach(() => vi.stubEnv("CONVEX_TRADING_SECRET", "secret"));
afterEach(() => vi.unstubAllEnvs());

test("ownership is enforced on reads and writes", async () => {
  const t = convexTest(schema, modules);
  await t.withIdentity({ subject: "owner" }).mutation(save, draft);
  await expect(t.withIdentity({ subject: "other" }).mutation(save, draft)).rejects.toThrow("unavailable");
  expect((await t.withIdentity({ subject: "other" }).query(list, {
    defaults: false, paginationOpts: { numItems: 100, cursor: null },
  })).page).toEqual([]);
  await expect(t.mutation(save, draft)).rejects.toThrow("Sign in");
});

test("stale edits cannot overwrite newer versions and deletion retires selection", async () => {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ subject: "owner" });
  expect((await owner.mutation(save, draft)).version).toBe(1);
  const changed = { ...draft, definitionJson: JSON.stringify({ name: draft.name, enabledForAi: true, legs: [{ id: "put" }] }), expectedVersion: 1 };
  expect((await owner.mutation(save, changed)).version).toBe(2);
  await expect(owner.mutation(save, { ...draft, expectedVersion: 1 })).rejects.toThrow("another session");
  await expect(owner.mutation(remove, { id, expectedVersion: 1 })).rejects.toThrow("Reload");
  await owner.mutation(remove, { id, expectedVersion: 2 });
  expect(await t.query(get, { secret: "secret", userId: "owner", id })).toBeNull();
});

test("import preserves old identities and refuses to overwrite changes", async () => {
  const t = convexTest(schema, modules);
  const record = { id, user_id: null, name: draft.name, definitionJson: definition, source_run_id: null,
    version: 7, enabled_for_ai: true, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };
  await expect(t.mutation(importRows, { secret: "secret", records: [record] })).rejects.toThrow("Import disabled");
  vi.stubEnv("CONVEX_IMPORT_ENABLED", "true");
  await t.mutation(importRows, { secret: "secret", records: [record] });
  await t.mutation(importRows, { secret: "secret", records: [record] });
  await expect(t.mutation(importRows, { secret: "secret", records: [{ ...record, version: 8 }] })).rejects.toThrow("conflicts");
  expect((await t.withIdentity({ subject: "owner" }).query(list, {
    defaults: true, paginationOpts: { numItems: 100, cursor: null },
  })).page[0]).toMatchObject(record);
});

test("service updates one default definition with optimistic locking", async () => {
  const t = convexTest(schema, modules);
  const record = {
    id,
    user_id: null,
    name: draft.name,
    definitionJson: definition,
    source_run_id: null,
    version: 7,
    enabled_for_ai: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    deleted: false,
  };
  await t.run(async ctx => {
    await ctx.db.insert("savedStrategies", record);
  });
  const changedDefinition = JSON.stringify({
    name: draft.name,
    enabledForAi: true,
    legs: [{ id: "call" }],
    takeProfitPercent: 90,
    emergencyStopLossPercent: 170,
  });

  const updated = await t.mutation(updateDefault, {
    secret: "secret",
    id,
    definitionJson: changedDefinition,
    expectedVersion: 7,
  });

  expect(updated.name).toBe(draft.name);
  expect(updated.version).toBe(8);
  expect(updated.definitionJson).toBe(changedDefinition);
  await expect(t.mutation(updateDefault, {
    secret: "secret",
    id,
    definitionJson: JSON.stringify({
      name: draft.name,
      enabledForAi: true,
      legs: [{ id: "call" }],
      takeProfitPercent: 80,
      emergencyStopLossPercent: 170,
    }),
    expectedVersion: 7,
  })).rejects.toThrow("changed");
});
