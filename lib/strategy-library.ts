import { ConvexHttpClient } from "convex/browser";
import { makeFunctionReference, type PaginationResult } from "convex/server";
import type { Infer } from "convex/values";
import type { savedStrategyRecord } from "@/convex/applicationValidators";
import type { SavedStrategy } from "@/lib/app-types";
import type { StrategyDefinition } from "@/lib/strategy-types";
import type { Json, SavedStrategyRow } from "@/lib/supabase/types";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { requestJson } from "@/lib/api";

type LibraryRecord = Infer<typeof savedStrategyRecord>;
const list = makeFunctionReference<"query", { defaults: boolean; paginationOpts: { numItems: number; cursor: string | null } }, PaginationResult<LibraryRecord>>("library:list");
const save = makeFunctionReference<"mutation", {
  id: string; name: string; definitionJson: string; enabled: boolean; expectedVersion: number | null;
}, LibraryRecord>("library:save");
const remove = makeFunctionReference<"mutation", { id: string; expectedVersion: number }, null>("library:remove");
const recoveryList = makeFunctionReference<"query", { entityType: "saved_strategies" }, Array<{
  summary: unknown; updatedAt: number;
}>>("recovery:readOnlyForUser");
const localStorageEnabled = process.env.NEXT_PUBLIC_APPLICATION_STORAGE === "local";

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

function recoveryRow(value: unknown): SavedStrategyRow | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (!("id" in value) || typeof value.id !== "string" ||
      !("name" in value) || typeof value.name !== "string" ||
      !("definition_json" in value) || !isJson(value.definition_json) ||
      !("version" in value) || typeof value.version !== "number" ||
      !("enabled_for_ai" in value) || typeof value.enabled_for_ai !== "boolean" ||
      !("created_at" in value) || typeof value.created_at !== "string" ||
      !("updated_at" in value) || typeof value.updated_at !== "string") return null;
  const owner = "user_id" in value ? value.user_id : null;
  const source = "source_run_id" in value ? value.source_run_id : null;
  if (owner !== null && typeof owner !== "string") return null;
  if (source !== null && typeof source !== "string") return null;
  return {
    id: value.id, user_id: owner, name: value.name, definition_json: value.definition_json,
    source_run_id: source, version: value.version, enabled_for_ai: value.enabled_for_ai,
    created_at: value.created_at, updated_at: value.updated_at,
  };
}

export type StrategyLibraryRead = { rows: SavedStrategyRow[]; recoveryAt: number | null };

export async function readStrategyLibrary(): Promise<StrategyLibraryRead> {
  if (localStorageEnabled) {
    try {
      const response = await requestJson<{ result: SavedStrategyRow[] }>("/api/library");
      return { rows: response.result, recoveryAt: null };
    } catch (backendError) {
      try {
        const mirrored = await (await convexLibrary()).query(recoveryList, { entityType: "saved_strategies" });
        const rows = mirrored.map(item => recoveryRow(item.summary)).filter((row): row is SavedStrategyRow => row !== null);
        if (!rows.length) throw backendError;
        return { rows, recoveryAt: Math.max(...mirrored.map(item => item.updatedAt)) };
      } catch {
        throw backendError;
      }
    }
  }
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
  return { rows: groups.flat(), recoveryAt: null };
}

export async function saveLibraryStrategy(definition: StrategyDefinition, existing: SavedStrategy | undefined, isOwner = false): Promise<SavedStrategyRow> {
  const editable = existing && (!existing.isDefault || isOwner) ? existing : undefined;
  if (localStorageEnabled) {
    const id = editable?.id ?? crypto.randomUUID();
    const response = await requestJson<{ result: SavedStrategyRow }>(`/api/library/${id}`, {
      method: "PUT", body: JSON.stringify({ id, name: definition.name, definitionJson: JSON.stringify(definition),
        enabled: definition.enabledForAi, expectedVersion: editable?.version ?? null }),
    });
    return response.result;
  }
  const client = await convexLibrary();
  return legacyRow(await client.mutation(save, {
    id: editable?.id ?? crypto.randomUUID(), name: definition.name,
    definitionJson: JSON.stringify(definition), enabled: definition.enabledForAi,
    expectedVersion: editable?.version ?? null,
  }));
}

export async function deleteLibraryStrategy(strategy: SavedStrategy, isOwner = false): Promise<void> {
  if (strategy.isDefault && !isOwner) throw new Error("Default strategies cannot be deleted");
  if (localStorageEnabled) {
    await requestJson(`/api/library/${strategy.id}?expectedVersion=${strategy.version}`, { method: "DELETE" });
    return;
  }
  await (await convexLibrary()).mutation(remove, { id: strategy.id, expectedVersion: strategy.version });
}
