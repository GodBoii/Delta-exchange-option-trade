import type { AgentAsset, AutomationOverview } from "@/lib/app-types";

/** Older payloads omit the asset; those runs and proposals came from the BTC agent. */
export function assetOf(record: { asset?: AgentAsset | null }): AgentAsset {
  return record.asset ?? "BTC";
}

export type UpcomingRun = AutomationOverview["upcomingRuns"][number];

/**
 * One scheduled session. The BTC and ETH agents run the same session at the
 * same instant, so they share a row instead of printing the time twice.
 */
export type ScheduleSlot = {
  key: string;
  scheduledFor: string;
  trigger: string;
  assets: AgentAsset[];
};

export function groupScheduleSlots(runs: readonly UpcomingRun[]): ScheduleSlot[] {
  const slots = new Map<string, ScheduleSlot>();
  for (const run of runs) {
    const key = `${run.scheduledFor}|${run.trigger}`;
    const asset = assetOf(run);
    const slot = slots.get(key);
    if (!slot) {
      slots.set(key, { key, scheduledFor: run.scheduledFor, trigger: run.trigger, assets: [asset] });
    } else if (!slot.assets.includes(asset)) {
      slot.assets.push(asset);
    }
  }
  return [...slots.values()]
    .map(slot => ({ ...slot, assets: [...slot.assets].sort() }))
    .sort((a, b) => new Date(a.scheduledFor).getTime() - new Date(b.scheduledFor).getTime());
}

export type AssetFilter = "all" | AgentAsset;

export function filterByAsset<T extends { asset?: AgentAsset | null }>(records: readonly T[], filter: AssetFilter): T[] {
  return filter === "all" ? [...records] : records.filter(record => assetOf(record) === filter);
}

/** Count per filter option, so each tab can show how many records it holds. */
export function countByAsset(records: readonly { asset?: AgentAsset | null }[]): Record<AssetFilter, number> {
  const counts: Record<AssetFilter, number> = { all: records.length, BTC: 0, ETH: 0 };
  for (const record of records) counts[assetOf(record)] += 1;
  return counts;
}
