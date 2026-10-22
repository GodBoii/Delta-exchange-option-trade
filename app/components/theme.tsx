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
import {
  isThemeChoice, saveThemeChoice, storedThemeChoice, THEME_SCHEME_QUERY, THEME_STORAGE_KEY,
  type ResolvedTheme, type ThemeChoice,
} from "@/lib/theme-preference";
export { THEME_BOOT_SCRIPT, type ResolvedTheme, type ThemeChoice } from "@/lib/theme-preference";

type ThemeContextValue = {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setChoice: (choice: ThemeChoice) => void;
  preferenceSaved: boolean;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia(THEME_SCHEME_QUERY).matches ? "light" : "dark";
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
  const [preferenceSaved, setPreferenceSaved] = useState(true);

  useEffect(() => {
    const query = window.matchMedia(THEME_SCHEME_QUERY);
    const onChange = () => setSystem(query.matches ? "light" : "dark");
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY || event.key === null) setChoiceState(storedThemeChoice());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
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
    setPreferenceSaved(saveThemeChoice(next));
  }, []);

  const value = useMemo(() => ({ choice, resolved, setChoice, preferenceSaved }), [choice, resolved, setChoice, preferenceSaved]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
