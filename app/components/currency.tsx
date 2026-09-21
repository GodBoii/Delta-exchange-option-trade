"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode
} from "react";
import { EM_DASH, toNumber } from "@/lib/format";

export type DisplayCurrency = "USD" | "INR";
type RateState =
  | { kind: "loading" }
  | { kind: "ready"; rate: number; date: string | null }
  | { kind: "error" };

type MoneyOptions = {
  digits?: number;
  signed?: boolean;
};

type CurrencyContextValue = {
  currency: DisplayCurrency;
  currencyCode: DisplayCurrency;
  rateState: RateState;
  setCurrency: (currency: DisplayCurrency) => void;
  convertFromUsd: (value: number) => number;
  convertToUsd: (value: number) => number;
  formatMoney: (value: unknown, options?: MoneyOptions) => string;
  formatMoneyNumber: (value: unknown, options?: MoneyOptions) => string;
};

const PREFERENCE_KEY = "trade-cognition-display-currency";
const RATE_KEY = "trade-cognition-usd-inr-rate";
const MAX_STORED_RATE_AGE_MS = 2 * 60 * 60 * 1_000;
const CurrencyContext = createContext<CurrencyContextValue | null>(null);

function isDisplayCurrency(value: unknown): value is DisplayCurrency {
  return value === "USD" || value === "INR";
}

function readStoredRate(): RateState {
  if (typeof window === "undefined") return { kind: "loading" };
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(RATE_KEY) ?? "null");
    if (!parsed || typeof parsed !== "object") return { kind: "loading" };
    const rate = "rate" in parsed ? parsed.rate : null;
    const date = "date" in parsed ? parsed.date : null;
    const storedAt = "storedAt" in parsed ? parsed.storedAt : null;
    return typeof rate === "number" && Number.isFinite(rate) && rate > 0
      && typeof storedAt === "number" && Number.isFinite(storedAt)
      && storedAt <= Date.now() && Date.now() - storedAt < MAX_STORED_RATE_AGE_MS
      ? { kind: "ready", rate, date: typeof date === "string" ? date : null }
      : { kind: "loading" };
  } catch {
    return { kind: "loading" };
  }
}

function formatNumber(value: number, currency: DisplayCurrency, digits: number, signed: boolean, style: "currency" | "decimal") {
  return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", {
    style,
    ...(style === "currency" ? { currency } : {}),
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: signed ? "exceptZero" : "auto"
  }).format(value);
}

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [currency, setCurrencyState] = useState<DisplayCurrency>("USD");
  const [rateState, setRateState] = useState<RateState>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    try {
      const stored = window.localStorage.getItem(PREFERENCE_KEY);
      if (isDisplayCurrency(stored)) setCurrencyState(stored);
    } catch { /* A blocked preference store keeps the USD default. */ }
    setRateState(readStoredRate());
    fetch("/api/exchange-rate")
      .then(async response => {
        if (!response.ok) throw new Error("Exchange rate unavailable");
        const payload: unknown = await response.json();
        if (!payload || typeof payload !== "object" || !("rate" in payload)) throw new Error("Invalid exchange rate");
        const rate = payload.rate;
        const date = "date" in payload ? payload.date : null;
        if (typeof rate !== "number" || !Number.isFinite(rate) || rate <= 0) throw new Error("Invalid exchange rate");
        return { rate, date: typeof date === "string" ? date : null };
      })
      .then(result => {
        if (!active) return;
        const next: RateState = { kind: "ready", ...result };
        setRateState(next);
        try { window.localStorage.setItem(RATE_KEY, JSON.stringify({ ...result, storedAt: Date.now() })); } catch { /* Session value still works. */ }
      })
      .catch(() => {
        if (!active) return;
        setRateState(previous => previous.kind === "ready" ? previous : { kind: "error" });
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (rateState.kind === "error") setCurrencyState("USD");
  }, [rateState.kind]);

  const setCurrency = useCallback((next: DisplayCurrency) => {
    setCurrencyState(next);
    try { window.localStorage.setItem(PREFERENCE_KEY, next); } catch { /* Session preference still works. */ }
  }, []);

  const rate = rateState.kind === "ready" ? rateState.rate : null;
  const currencyCode: DisplayCurrency = currency === "INR" && rate !== null ? "INR" : "USD";
  const convertFromUsd = useCallback((value: number) => currencyCode === "INR" && rate !== null ? value * rate : value, [currencyCode, rate]);
  const convertToUsd = useCallback((value: number) => currencyCode === "INR" && rate !== null ? value / rate : value, [currencyCode, rate]);

  const formatMoney = useCallback((value: unknown, options: MoneyOptions = {}) => {
    const parsed = toNumber(value);
    if (parsed === null) return EM_DASH;
    return formatNumber(convertFromUsd(parsed), currencyCode, options.digits ?? 2, options.signed ?? false, "currency");
  }, [convertFromUsd, currencyCode]);

  const formatMoneyNumber = useCallback((value: unknown, options: MoneyOptions = {}) => {
    const parsed = toNumber(value);
    if (parsed === null) return EM_DASH;
    return formatNumber(convertFromUsd(parsed), currencyCode, options.digits ?? 2, options.signed ?? false, "decimal");
  }, [convertFromUsd, currencyCode]);

  const value = useMemo(() => ({
    currency, currencyCode, rateState, setCurrency, convertFromUsd, convertToUsd, formatMoney, formatMoneyNumber
  }), [currency, currencyCode, rateState, setCurrency, convertFromUsd, convertToUsd, formatMoney, formatMoneyNumber]);

  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
}

export function useCurrency() {
  const value = useContext(CurrencyContext);
  if (!value) throw new Error("useCurrency must be used inside CurrencyProvider");
  return value;
}
