import type { SavedStrategy } from "@/lib/app-types";
import type { StrategyDefinition } from "@/lib/strategy-types";
import type { SavedStrategyRow } from "@/lib/supabase/types";
import { requestJson } from "@/lib/api";

/** Identity of a definition independent of its stored revision number, so revisions do not trigger autosave. */
export const definitionFingerprint = (strategy: StrategyDefinition) => JSON.stringify({ ...strategy, version: 0 });

/** The signed-in user's saved strategies plus the shared templates, from the trading backend. */
export async function readStrategyLibrary(): Promise<SavedStrategyRow[]> {
  const response = await requestJson<{ result: SavedStrategyRow[] }>("/api/library");
  return response.result;
}

export async function saveLibraryStrategy(definition: StrategyDefinition, existing: SavedStrategy | undefined, isOwner = false): Promise<SavedStrategyRow> {
  const editable = existing && (!existing.isDefault || isOwner) ? existing : undefined;
  const id = editable?.id ?? crypto.randomUUID();
  const response = await requestJson<{ result: SavedStrategyRow }>(`/api/library/${id}`, {
    method: "PUT", body: JSON.stringify({ id, name: definition.name, definitionJson: JSON.stringify(definition),
      enabled: definition.enabledForAi, expectedVersion: editable?.version ?? null }),
  });
  return response.result;
}

export async function deleteLibraryStrategy(strategy: SavedStrategy, isOwner = false): Promise<void> {
  if (strategy.isDefault && !isOwner) throw new Error("Default strategies cannot be deleted");
  await requestJson(`/api/library/${strategy.id}?expectedVersion=${strategy.version}`, { method: "DELETE" });
}
