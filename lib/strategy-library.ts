import { ConvexHttpClient } from "convex/browser";
import { makeFunctionReference, type PaginationResult } from "convex/server";
import type { Infer } from "convex/values";
import type { savedStrategyRecord } from "@/convex/applicationValidators";
import type { SavedStrategy } from "@/lib/app-types";
import type { StrategyDefinition } from "@/lib/strategy-types";
import type { Json, SavedStrategyRow } from "@/lib/supabase/types";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

type LibraryRecord = Infer<typeof savedStrategyRecord>;
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

export async function saveLibraryStrategy(definition: StrategyDefinition, existing: SavedStrategy | undefined, isOwner = false): Promise<SavedStrategyRow> {
  const editable = existing && (!existing.isDefault || isOwner) ? existing : undefined;
  const client = await convexLibrary();
  return legacyRow(await client.mutation(save, {
    id: editable?.id ?? crypto.randomUUID(), name: definition.name,
    definitionJson: JSON.stringify(definition), enabled: definition.enabledForAi,
    expectedVersion: editable?.version ?? null,
  }));
}

export async function deleteLibraryStrategy(strategy: SavedStrategy, isOwner = false): Promise<void> {
  if (strategy.isDefault && !isOwner) throw new Error("Default strategies cannot be deleted");
  await (await convexLibrary()).mutation(remove, { id: strategy.id, expectedVersion: strategy.version });
}
