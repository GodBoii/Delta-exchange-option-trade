import { ConvexHttpClient } from "convex/browser";
import { makeFunctionReference, type PaginationResult } from "convex/server";
import type { Infer } from "convex/values";
import type { savedStrategyRecord } from "@/convex/applicationValidators";
import type { SavedStrategy } from "@/lib/app-types";
import type { StrategyDefinition } from "@/lib/strategy-types";
import type { Json, SavedStrategyRow } from "@/lib/supabase/types";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

type LibraryRecord = Infer<typeof savedStrategyRecord>;
const useConvex = process.env.NEXT_PUBLIC_CONVEX_LIBRARY_ENABLED === "true";
const columns = "id,user_id,name,definition_json,source_run_id,version,enabled_for_ai,created_at,updated_at";
const list = makeFunctionReference<"query", { defaults: boolean; paginationOpts: { numItems: number; cursor: string | null } }, PaginationResult<LibraryRecord>>("library:list");
const save = makeFunctionReference<"mutation", {
  id: string; name: string; definitionJson: string; enabled: boolean; expectedVersion: number | null;
}, LibraryRecord>("library:save");
const remove = makeFunctionReference<"mutation", { id: string; expectedVersion: number }, null>("library:remove");

export const definitionFingerprint = (strategy: StrategyDefinition) => JSON.stringify({ ...strategy, version: 0 });

function isJson(value: unknown): value is Json {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJson);
  return typeof value === "object" && Object.values(value).every(isJson);
}

function jsonDefinition(text: string): Json {
  const value: unknown = JSON.parse(text);
  if (!isJson(value)) throw new Error("Invalid saved definition");
  return value;
}

function legacyRow(record: LibraryRecord): SavedStrategyRow {
  const { definitionJson, ...metadata } = record;
  return { ...metadata, definition_json: jsonDefinition(definitionJson) };
}

async function convexLibrary() {
  const url = process.env.NEXT_PUBLIC_CONVEX_URL;
  if (!url) throw new Error("Convex library is enabled but its URL is missing");
  const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
  if (!session) throw new Error("Sign in to use the strategy library");
  const client = new ConvexHttpClient(url);
  client.setAuth(session.access_token);
  return client;
}

export async function readStrategyLibrary(): Promise<SavedStrategyRow[]> {
  if (useConvex) {
    const client = await convexLibrary();
    const groups = await Promise.all([false, true].map(async defaults => {
      const rows: LibraryRecord[] = [];
      let cursor: string | null = null;
      for (;;) {
        const page: PaginationResult<LibraryRecord> = await client.query(list, { defaults, paginationOpts: { numItems: 100, cursor } });
        rows.push(...page.page);
        if (page.isDone) return rows.map(legacyRow);
        if (page.continueCursor === cursor) throw new Error("Library pagination did not advance");
        cursor = page.continueCursor;
      }
    }));
    return groups.flat();
  }
  const rows: SavedStrategyRow[] = [];
  for (let from = 0; ; from += 500) {
    const { data, error } = await getSupabaseBrowserClient().from("saved_strategies")
      .select(columns).order("updated_at", { ascending: false }).range(from, from + 499);
    if (error) throw error;
    rows.push(...data);
    if (data.length < 500) return rows;
  }
}

export async function saveLibraryStrategy(definition: StrategyDefinition, existing: SavedStrategy | undefined, userId: string): Promise<SavedStrategyRow> {
  const editable = existing && !existing.isDefault ? existing : undefined;
  if (useConvex) {
    const client = await convexLibrary();
    return legacyRow(await client.mutation(save, {
      id: editable?.id ?? crypto.randomUUID(), name: definition.name,
      definitionJson: JSON.stringify(definition), enabled: definition.enabledForAi,
      expectedVersion: editable?.version ?? null,
    }));
  }
  const value = { name: definition.name, definition_json: jsonDefinition(JSON.stringify(definition)), enabled_for_ai: definition.enabledForAi };
  const supabase = getSupabaseBrowserClient();
  const { data, error } = editable
    ? await supabase.from("saved_strategies").update(value).eq("id", editable.id).eq("user_id", userId).select(columns).single()
    : await supabase.from("saved_strategies").insert({ user_id: userId, ...value }).select(columns).single();
  if (error) throw error;
  return data;
}

export async function deleteLibraryStrategy(strategy: SavedStrategy, userId: string): Promise<void> {
  if (strategy.isDefault) throw new Error("Default strategies cannot be deleted");
  if (useConvex) {
    await (await convexLibrary()).mutation(remove, { id: strategy.id, expectedVersion: strategy.version });
    return;
  }
  const { error } = await getSupabaseBrowserClient().from("saved_strategies").delete().eq("id", strategy.id).eq("user_id", userId);
  if (error) throw error;
}
