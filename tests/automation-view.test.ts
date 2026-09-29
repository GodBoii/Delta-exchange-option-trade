import { expect, test } from "vitest";
import { countByAsset, filterByAsset, groupScheduleSlots } from "../lib/automation-view";

test("BTC and ETH runs of the same session share one slot", () => {
  const slots = groupScheduleSlots([
    { id: "2", asset: "ETH", trigger: "london_session", scheduledFor: "2026-09-30T07:00:00Z" },
    { id: "1", asset: "BTC", trigger: "asia_session", scheduledFor: "2026-09-30T00:00:00Z" },
    { id: "3", asset: "ETH", trigger: "asia_session", scheduledFor: "2026-09-30T00:00:00Z" },
    { id: "4", asset: "BTC", trigger: "london_session", scheduledFor: "2026-09-30T07:00:00Z" }
  ]);
  expect(slots.map(slot => [slot.trigger, slot.assets])).toEqual([
    ["asia_session", ["BTC", "ETH"]],
    ["london_session", ["BTC", "ETH"]]
  ]);
});

test("runs without an asset count as BTC", () => {
  const records = [{ asset: undefined }, { asset: "ETH" as const }, { asset: "BTC" as const }];
  expect(countByAsset(records)).toEqual({ all: 3, BTC: 2, ETH: 1 });
  expect(filterByAsset(records, "BTC")).toHaveLength(2);
  expect(filterByAsset(records, "all")).toHaveLength(3);
});
