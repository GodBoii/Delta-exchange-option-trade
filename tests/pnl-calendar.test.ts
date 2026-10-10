import { expect, test } from "vitest";
import { calendarBounds, calendarDateLabel, calendarDates, calendarYears, dayTone, parsePnlCalendar } from "../lib/pnl-calendar";
import type { PnlDay } from "../lib/app-types";

const day: PnlDay = {
  date: "2026-10-01", netRealizedPnl: "1", grossGains: "1", grossLosses: "0", exchangeFees: "0.2",
  settledRuns: 1, wins: 1, losses: 0, breakEven: 0
};

test("calendar includes every day, pads Monday-based weeks, and crosses years", () => {
  const dates = calendarDates("2025-12-30", "2026-01-02");
  expect(dates).toEqual(["2025-12-29", "2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]);
  expect(dates.length % 7).toBe(0);
});

test("calendar arithmetic handles leap days without depending on the browser timezone", () => {
  expect(calendarDates("2024-02-28", "2024-03-01")).toContain("2024-02-29");
  expect(calendarDates("2012-01-01", "2012-12-31")).toHaveLength(378);
  expect(calendarDateLabel("2024-02-29")).toContain("29 Feb 2024");
  expect(calendarDates("invalid", "2026-10-01")).toEqual([]);
  expect(calendarDates("2026-10-02", "2026-10-01")).toEqual([]);
});

test("calendar responses reject malformed money, duplicate days and invalid counts", () => {
  const response = { success: true, range: "30d", asset: "all", strategy: null, timezone: "Asia/Kolkata",
    asOf: "2026-10-10T12:00:00Z", startDate: "2026-09-10", endDate: "2026-10-10",
    historyComplete: true, historyVerifiedAt: null, days: [day] };
  expect(parsePnlCalendar(response).days).toEqual([day]);
  expect(() => parsePnlCalendar({ ...response, days: [{ ...day, netRealizedPnl: "NaN" }] })).toThrow(/amount/);
  expect(() => parsePnlCalendar({ ...response, days: [day, day] })).toThrow(/settlement day/);
  expect(() => parsePnlCalendar({ ...response, days: [{ ...day, wins: 3 }] })).toThrow(/outcome counts/);
  expect(() => parsePnlCalendar({ ...response, days: [{ ...day, date: "2026-02-30" }] })).toThrow(/calendar date/);
  expect(() => parsePnlCalendar({ ...response, timezone: "UTC" })).toThrow(/response/);
});

test("profit and loss use symmetric intensity levels and zero differs from no activity", () => {
  expect(dayTone(undefined, 100)).toBe("empty");
  expect(dayTone({ ...day, netRealizedPnl: "0" }, 100)).toBe("even");
  for (const [amount, level] of [[10, 1], [40, 2], [70, 3], [100, 4]]) {
    expect(dayTone({ ...day, netRealizedPnl: String(amount) }, 100)).toBe(`gain-${level}`);
    expect(dayTone({ ...day, netRealizedPnl: String(-amount) }, 100)).toBe(`loss-${level}`);
  }
});

test("year options include empty intervening years and the current year", () => {
  expect(calendarYears([], "2026-10-10")).toEqual([2026]);
  expect(calendarYears([{ ...day, date: "2023-12-31" }], "2026-10-10")).toEqual([2026, 2025, 2024, 2023]);
});

test("calendar selection covers a complete year or exactly one month, including leap years", () => {
  expect(calendarBounds(2026, null)).toEqual({ start: "2026-01-01", end: "2026-12-31" });
  expect(calendarBounds(2024, 1)).toEqual({ start: "2024-02-01", end: "2024-02-29" });
  expect(calendarBounds(2026, 1)).toEqual({ start: "2026-02-01", end: "2026-02-28" });
  expect(calendarBounds(2025, 11)).toEqual({ start: "2025-12-01", end: "2025-12-31" });
  const { start, end } = calendarBounds(2026, 9);
  expect(calendarDates(start, end).filter(date => date >= start && date <= end)).toHaveLength(31);
});
