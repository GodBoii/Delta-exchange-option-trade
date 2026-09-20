/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const write = makeFunctionReference<"mutation">("runtimeRecords:write");
const update = makeFunctionReference<"mutation">("runtimeRecords:update");
const select = makeFunctionReference<"query">("runtimeRecords:select");
const reserve = makeFunctionReference<"mutation">("runtimeControl:reserveCapital");
const claim = makeFunctionReference<"mutation">("runtimeControl:claimAgent");
const args = { secret: "test" };
beforeEach(() => vi.stubEnv("CONVEX_TRADING_SECRET", args.secret));
afterEach(() => vi.unstubAllEnvs());

test("capital cannot be reserved twice or beyond account capacity", async () => {
  const t = convexTest(schema, modules);
  await t.run(async ctx => { await ctx.db.insert("exchangeConnections", { id: "connection", user_id: "owner", delta_user_id: "account",
    account_name: "Main", email_masked: null, environment: "production", status: "connected", ciphertext: "encrypted", fingerprint: "fp", updated_at: "date" }); });
  for (const id of ["first", "second"]) await t.mutation(write, { ...args, table: "strategies", rowJson: JSON.stringify({ id, user_id: "owner", status: "scheduled" }) });
  const request = { ...args, p_user_id: "owner", p_strategy_id: "first", p_maximum_slots: 1, p_budget: "50", p_total_balance: "100" };
  expect((await t.mutation(reserve, request)).created).toBe(true);
  expect((await t.mutation(reserve, request)).created).toBe(false);
  await expect(t.mutation(reserve, { ...request, p_strategy_id: "second" })).rejects.toThrow("occupied");
  await t.mutation(update, { ...args, table: "strategies", ids: ["first"], conditions: [], patchJson: '{"status":"completed"}' });
  expect((await t.mutation(reserve, { ...request, p_strategy_id: "second" })).created).toBe(true);
});

test("claim compare-and-set runs inside the mutation", async () => {
  const t = convexTest(schema, modules);
  await t.mutation(write, { ...args, table: "strategies", rowJson: '{"id":"run","user_id":"owner","status":"active"}' });
  const input = { ...args, table: "strategies", ids: ["run"],
    conditions: [{ field: "status", op: "eq", valueJson: '"active"' }], patchJson: '{"status":"executing_exit"}' };
  expect(await t.mutation(update, input)).toHaveLength(1);
  expect(await t.mutation(update, input)).toHaveLength(0);
});

test("independent reviews run concurrently but each run is claimed once", async () => {
  const t = convexTest(schema, modules);
  for (const id of ["a", "b"]) await t.mutation(write, { ...args, table: "automation_agent_runs",
    rowJson: JSON.stringify({ id, user_id: "owner", status: "scheduled", run_key: id }) });
  expect(await t.mutation(claim, { ...args, p_user_id: "owner", p_run_id: "a" })).toHaveLength(1);
  expect(await t.mutation(claim, { ...args, p_user_id: "owner", p_run_id: "b" })).toHaveLength(1);
  expect(await t.mutation(claim, { ...args, p_user_id: "owner", p_run_id: "a" })).toHaveLength(0);
});

test("pending rechecks migrate to seven minutes without changing running runs", async () => {
  const t = convexTest(schema, modules);
  const activation = Date.now() + 900000;
  await t.mutation(write, { ...args, table: "strategy_proposals",
    rowJson: JSON.stringify({ id: "proposal", user_id: "owner", activation_time: new Date(activation).toISOString() }) });
  for (const status of ["scheduled", "running"]) await t.mutation(write, { ...args, table: "automation_agent_runs",
    rowJson: JSON.stringify({ id: status, user_id: "owner", status, trigger: "activation_recheck",
      strategy_proposal_id: "proposal", scheduled_for: new Date(activation - 300000).toISOString() }) });
  const migrate = makeFunctionReference<"mutation">("runtimeControl:reschedulePendingRechecks");
  expect(await t.mutation(migrate, { ...args, cursor: null })).toMatchObject({ updated: 1, isDone: true });
  await t.run(async ctx => {
    const rows = await ctx.db.query("automation_agent_runs").collect();
    expect(rows.find(row => row.externalId === "scheduled")?.time).toBe(activation - 420000);
    expect(rows.find(row => row.externalId === "running")?.time).toBe(activation - 300000);
  });
  expect(await t.mutation(migrate, { ...args, cursor: null })).toMatchObject({ updated: 0 });
});

test("upsert preserves existing policy fields and null filters match absent fields", async () => {
  const t = convexTest(schema, modules);
  await t.mutation(write, { ...args, table: "automation_settings", rowJson: '{"id":"owner","user_id":"owner","maximum_agent_runs_per_day":2,"enabled":true}' });
  await t.mutation(write, { ...args, table: "automation_settings", conflict: "user_id", rowJson: '{"id":"owner","user_id":"owner","enabled":false}', defaultsJson: '{"maximum_agent_runs_per_day":3}' });
  const result = await t.query(select, { ...args, table: "automation_settings", conditions: [{ field: "missing", op: "is", valueJson: "null" }], paginationOpts: { numItems: 100, cursor: null } });
  expect(JSON.parse(result.page[0])).toMatchObject({ maximum_agent_runs_per_day: 2, enabled: false });
});

test("large reports are excluded from scheduling scans and preserved on status updates", async () => {
  const t = convexTest(schema, modules);
  const report = "r".repeat(550000);
  await t.mutation(write, { ...args, table: "automation_agent_runs", rowJson: JSON.stringify({ id: "large", user_id: "owner", status: "running", report_markdown: report }) });
  await t.mutation(update, { ...args, table: "automation_agent_runs", ids: ["large"], conditions: [], patchJson: '{"status":"completed"}' });
  const input = { ...args, table: "automation_agent_runs", conditions: [], paginationOpts: { numItems: 100, cursor: null } };
  const metadata = await t.query(select, { ...input, columns: "id,status" });
  expect(metadata.page[0].length).toBeLessThan(1000);
  const full = await t.query(select, { ...input, columns: "*" });
  expect(JSON.parse(full.page[0]).report_markdown).toBe(report);
  expect(JSON.parse(full.page[0]).status).toBe("completed");
});

test("import does not rewrite source data or overwrite a newer record", async () => {
  const t = convexTest(schema, modules);
  vi.stubEnv("CONVEX_IMPORT_ENABLED", "true");
  const row = { id: "imported", user_id: "owner", status: "completed", created_at: "2026-08-01T00:00:00Z", result_json: { realizedPnl: "-1.97160990" } };
  const input = { ...args, table: "strategies", rowJson: JSON.stringify(row), importOnly: true };
  await t.mutation(write, input);
  await t.mutation(write, input);
  const output = await t.query(select, { ...args, table: "strategies", conditions: [], paginationOpts: { numItems: 100, cursor: null } });
  expect(JSON.parse(output.page[0])).toEqual(row);
  await expect(t.mutation(write, { ...input, rowJson: JSON.stringify({ ...row, status: "active" }) })).rejects.toThrow("conflicts");
});
