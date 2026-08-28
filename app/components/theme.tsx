"use client";

/**
 * Appearance.
 *
 * Three states, not a boolean: following the operating system is a real choice
 * and collapsing it into "dark on/off" loses it. The resolved value is written
 * to `data-theme` on the document element, which is the single hook the
 * stylesheet reads, so no component branches on the theme in JavaScript.
 *
 * Trading screens are read for hours at a time and often in a bright room, so
 * the light theme is a first-class surface here rather than an inverted
 * afterthought: it keeps the same semantic tokens and only re-points them.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode
} from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "trade-cognition-theme";
const SCHEME_QUERY = "(prefers-color-scheme: light)";

function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

/**
 * Runs before first paint, so the stored choice is on the document element
 * before any surface renders and the page never flashes the wrong theme. It is
 * duplicated logic on purpose: the alternative is a hydration-time correction,
 * which is exactly the flash this avoids.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{
var s=localStorage.getItem(${JSON.stringify(STORAGE_KEY)});
var c=(s==="light"||s==="dark"||s==="system")?s:"dark";
var r=c==="system"?(matchMedia(${JSON.stringify(SCHEME_QUERY)}).matches?"light":"dark"):c;
var e=document.documentElement;
e.dataset.theme=r;e.dataset.themeChoice=c;e.style.colorScheme=r;
}catch(_){}})();`;

type ThemeContextValue = {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setChoice: (choice: ThemeChoice) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia(SCHEME_QUERY).matches ? "light" : "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  /**
   * Seeded from the attribute the boot script already wrote rather than from a
   * constant, so the first client render agrees with the painted document and
   * there is nothing to correct.
   */
  const [choice, setChoiceState] = useState<ThemeChoice>(() => {
    if (typeof document === "undefined") return "dark";
    const attribute = document.documentElement.dataset.themeChoice;
    return isThemeChoice(attribute) ? attribute : "dark";
  });
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);

  useEffect(() => {
    const query = window.matchMedia(SCHEME_QUERY);
    const onChange = () => setSystem(query.matches ? "light" : "dark");
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const resolved: ResolvedTheme = choice === "system" ? system : choice;

  useEffect(() => {
    const element = document.documentElement;
    element.dataset.theme = resolved;
    element.dataset.themeChoice = choice;
    element.style.colorScheme = resolved;
  }, [choice, resolved]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // A blocked storage quota must not stop the theme from applying for the
      // rest of the session.
    }
  }, []);

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
