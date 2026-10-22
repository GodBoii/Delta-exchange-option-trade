import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { saveThemeChoice, storedThemeChoice, THEME_BOOT_SCRIPT, THEME_STORAGE_KEY } from "../lib/theme-preference";

let local: string | null, cookie: string;
let blockLocalWrite: boolean, blockCookieWrite: boolean;
const storage = {
  getItem: () => local,
  setItem: (_key: string, value: string) => {
    if (blockLocalWrite) throw new Error("QuotaExceededError");
    local = value;
  },
};
const root = () => ({ dataset: { theme: "", themeChoice: "" }, style: { colorScheme: "" } });
function boot(osLight = false) {
  const element = root();
  runInNewContext(THEME_BOOT_SCRIPT, { localStorage: storage, document: { cookie, documentElement: element },
    matchMedia: () => ({ matches: osLight }) });
  return element.dataset;
}

beforeEach(() => {
  local = null; cookie = ""; blockLocalWrite = false; blockCookieWrite = false;
  vi.stubGlobal("window", { localStorage: storage, location: { protocol: "https:" } });
  vi.stubGlobal("document", Object.defineProperty({}, "cookie", {
    get: () => cookie, set: (value: string) => { if (!blockCookieWrite) cookie = value.split(";")[0]; },
  }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

test("a cookie preserves Dark across reload when an old Light localStorage value cannot be replaced", () => {
  local = "light"; blockLocalWrite = true;
  expect(saveThemeChoice("dark")).toBe(true);
  expect(storedThemeChoice()).toBe("dark");
  expect(boot(true)).toMatchObject({ theme: "dark", themeChoice: "dark" });
});

test("a new local preference wins when cookies are blocked, including rapid changes", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-22T00:00:00Z"));
  expect(saveThemeChoice("light")).toBe(true);
  blockCookieWrite = true;
  expect(saveThemeChoice("dark")).toBe(true);
  expect(storedThemeChoice()).toBe("dark");
  expect(boot()).toMatchObject({ theme: "dark", themeChoice: "dark" });
});

test("failure of both stores is reported instead of claiming persistence", () => {
  local = "light"; blockLocalWrite = true; blockCookieWrite = true;
  expect(saveThemeChoice("dark")).toBe(false);
  expect(storedThemeChoice()).toBe("light");
});

test("cookies still restore the theme when localStorage reads are blocked", () => {
  saveThemeChoice("light");
  vi.spyOn(storage, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
  expect(storedThemeChoice()).toBe("light");
  expect(boot().theme).toBe("light");
});

test.each(["dark", "light", "system"])("legacy %s preferences survive and match the before-paint parser", choice => {
  local = choice;
  expect(storedThemeChoice()).toBe(choice);
  expect(boot(true).theme).toBe(choice === "system" ? "light" : choice);
});

test("corrupt values and malformed cookies cannot break startup", () => {
  local = '{"choice":"light","savedAt":"wrong"}'; cookie = `${THEME_STORAGE_KEY}=%invalid`;
  expect(storedThemeChoice()).toBe("dark");
  expect(boot().theme).toBe("dark");
});
