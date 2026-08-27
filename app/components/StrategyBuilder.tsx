"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown, ArrowUp, CalendarClock, ChevronDown, CircleDollarSign, Copy, Download,
  FileText, FolderOpen, Layers3, LoaderCircle, Maximize2, Plus, Save, Shield, ShieldCheck, Trash2,
  Upload, WifiOff
} from "lucide-react";
import type { StrategyDefinition, StrategyLeg } from "@/lib/strategy-types";
import type { SavedStrategy } from "@/lib/app-types";
import type { Json, SavedStrategyRow } from "@/lib/supabase/types";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { requestJson } from "@/lib/api";
import {
  errorMessage, formatDateTime, formatDuration, formatExpiry, relativeTime, toIso, toLocalInput
} from "@/lib/format";
import {
  AnimatedNumber, ClearableInput, ConfirmModal, Dialog, DrawnTick, EmptyState, Field, InlineMessage,
  MorphMenu, NumberField, OptionalNumberField, Panel, PanelHeader, SectionHeading, Segmented, Select,
  Shimmer, StatusDot, SuccessCheck, SwapText, Toggle, Tooltip, useBurst, useShake,
  type NoticeHandler
} from "@/app/components/ui";
import { BorderBeam } from "border-beam";

/* ------------------------------------------------------------------ *
 * Definition helpers
 * ------------------------------------------------------------------ */

const DRAFT_STORAGE_KEY = "delta-strategy-draft-v1";
const DRAFT_ID_STORAGE_KEY = "delta-strategy-draft-id-v1";
const MAX_LEGS = 12;
const DEFAULT_STRATEGY_NAMES = [
  "Long call", "Long put", "Long ATM straddle", "Long strangle",
  "Short ATM straddle", "Short strangle", "Iron condor", "Iron butterfly"
] as const;

const tomorrow = () => new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);

/** Local `datetime-local` value rounded up to the next five-minute boundary. */
const localDateTime = (offsetHours: number) => {
  const date = new Date(Date.now() + offsetHours * 3_600_000);
  date.setMinutes(Math.ceil(date.getMinutes() / 5) * 5, 0, 0);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

const uid = () => globalThis.crypto?.randomUUID?.().slice(0, 12) ?? Math.random().toString(36).slice(2, 14);

const newLeg = (overrides: Partial<StrategyLeg> = {}): StrategyLeg => ({
  id: uid(),
  lots: 1,
  position: "buy",
  optionType: "call",
  expiry: tomorrow(),
  strikeMode: "atm",
  strikeSteps: 0,
  orderType: "market_order",
  reentryOnTarget: 0,
  reentryOnStop: 0,
  ...overrides
});

const initialStrategy = (): StrategyDefinition => ({
  schemaVersion: 2,
  version: 1,
  name: "BTC ATM short straddle",
  description: "Sell the same-expiry ATM call and put when BTC is expected to remain in a tight range.",
  category: "premium_selling",
  marketOutlook: "sideways",
  enabledForAi: true,
  instrument: { index: "BTCUSD", underlying: "BTC", underlyingFrom: "cash" },
  entry: { strategyType: "intraday", entryAt: toIso(localDateTime(1)), exitAt: toIso(localDateTime(8)) },
  holdingMode: "hold_to_expiry",
  expiryPolicy: "same_day",
  exitMinutesBeforeExpiry: 5,
  sameExpiryRequired: true,
  squareOff: "complete",
  riskMode: "combined_premium",
  riskBasis: "net_credit",
  stopLossPercent: 100,
  takeProfitPercent: 50,
  combinedStopLossPercent: 100,
  emergencyStopLossPercent: 300,
  emergencyExitEnabled: true,
  trailToBreakEven: false,
  breakEvenScope: "all_legs",
  lotsMode: "auto",
  equalLotsRequired: true,
  legs: [
    newLeg({ position: "sell", optionType: "call", strikeMode: "atm", role: "short_call" }),
    newLeg({ position: "sell", optionType: "put", strikeMode: "atm", role: "short_put" })
  ],
  acknowledgement: true
});

function isStrategyDefinition(value: unknown): value is StrategyDefinition {
  if (!value || typeof value !== "object") return false;
  const strategy = value as Record<string, unknown>;
  return typeof strategy.name === "string"
    && Boolean(strategy.instrument && typeof strategy.instrument === "object")
    && Boolean(strategy.entry && typeof strategy.entry === "object")
    && Array.isArray(strategy.legs)
    && strategy.legs.length > 0;
}

function hydrateStrategy(
  strategy: StrategyDefinition & { allocationMode?: unknown; capitalAmount?: unknown }
): StrategyDefinition {
  const definition = { ...strategy };
  Reflect.deleteProperty(definition, "allocationMode");
  Reflect.deleteProperty(definition, "capitalAmount");
  const hasShortLeg = strategy.legs.some(leg => leg.position === "sell");
  return {
    ...definition,
    schemaVersion: 2,
    version: strategy.version ?? 1,
    description: strategy.description ?? "",
    category: strategy.category ?? (hasShortLeg ? "premium_selling" : "premium_buying"),
    marketOutlook: strategy.marketOutlook ?? (hasShortLeg ? "sideways" : "large_move_unknown_direction"),
    enabledForAi: strategy.enabledForAi ?? false,
    acknowledgement: true,
    holdingMode: strategy.holdingMode ?? "intraday",
    expiryPolicy: strategy.expiryPolicy ?? "same_day",
    exitMinutesBeforeExpiry: strategy.exitMinutesBeforeExpiry ?? 5,
    sameExpiryRequired: strategy.sameExpiryRequired ?? true,
    riskMode: strategy.riskMode ?? "legwise",
    riskBasis: strategy.riskBasis ?? (hasShortLeg ? "net_credit" : "net_debit"),
    stopLossPercent: Math.min(100, strategy.stopLossPercent ?? strategy.combinedStopLossPercent ?? 100),
    takeProfitPercent: strategy.takeProfitPercent ?? 50,
    combinedStopLossPercent: strategy.combinedStopLossPercent ?? undefined,
    emergencyStopLossPercent: strategy.emergencyStopLossPercent ?? undefined,
    emergencyExitEnabled: strategy.emergencyExitEnabled ?? true,
    lotsMode: strategy.lotsMode ?? "manual",
    equalLotsRequired: strategy.equalLotsRequired ?? false,
    legs: strategy.legs.map(leg => ({ ...leg, role: leg.role ?? undefined }))
  };
}

/** A saved definition whose window has passed is reopened with a fresh, equal-length window. */
function refreshExpiredSchedule(strategy: StrategyDefinition): StrategyDefinition {
  const now = Date.now();
  const entryAt = new Date(strategy.entry.entryAt).getTime();
  const exitAt = new Date(strategy.entry.exitAt).getTime();
  if (Number.isFinite(entryAt) && Number.isFinite(exitAt) && entryAt > now && exitAt > entryAt) return strategy;
  const freshEntryAt = toIso(localDateTime(1));
  const freshEntryMs = new Date(freshEntryAt).getTime();
  const previousDuration = Number.isFinite(entryAt) && Number.isFinite(exitAt) && exitAt > entryAt
    ? exitAt - entryAt
    : 7 * 3_600_000;
  return {
    ...strategy,
    entry: {
      ...strategy.entry,
      entryAt: freshEntryAt,
      exitAt: new Date(freshEntryMs + Math.max(previousDuration, 3_600_000)).toISOString()
    }
  };
}

const fingerprint = (strategy: StrategyDefinition) => JSON.stringify(strategy);

function savedStrategyFromRow(row: SavedStrategyRow): SavedStrategy | null {
  const definition = row.definition_json as unknown;
  if (!isStrategyDefinition(definition)) return null;
  return {
    id: row.id,
    isDefault: row.user_id === null || row.is_default === true,
    version: row.version,
    enabledForAi: row.enabled_for_ai,
    name: row.name,
    definition: { ...hydrateStrategy(definition), version: row.version, enabledForAi: row.enabled_for_ai },
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function sortSavedStrategies(left: SavedStrategy, right: SavedStrategy) {
  if (left.isDefault !== right.isDefault) return left.isDefault ? -1 : 1;
  return left.isDefault
    ? left.name.localeCompare(right.name)
    : right.updatedAt.localeCompare(left.updatedAt);
}

function markLegacyDefaultCopies(items: SavedStrategy[]) {
  if (items.some(item => item.isDefault)) return items;
  const legacyIds = new Set<string>();
  for (const name of DEFAULT_STRATEGY_NAMES) {
    const first = items
      .filter(item => item.name === name)
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt))[0];
    if (!first) return items;
    legacyIds.add(first.id);
  }
  return items.map(item => legacyIds.has(item.id) ? { ...item, isDefault: true } : item);
}

/* ------------------------------------------------------------------ *
 * Validation
 * ------------------------------------------------------------------ */

type ValidationIssue = { field: string; legId?: string; message: string };

/**
 * Blocking problems only. Each issue carries the exact control key so the
 * review rail can list it and the corresponding input can be highlighted, which
 * is faster than a single generic "check the form" error.
 */
function validate(strategy: StrategyDefinition): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (strategy.name.trim().length < 2) issues.push({ field: "name", message: "Give the strategy a name of at least two characters." });

  const entryAt = new Date(strategy.entry.entryAt).getTime();
  const exitAt = new Date(strategy.entry.exitAt).getTime();
  if (!Number.isFinite(entryAt)) issues.push({ field: "entryAt", message: "Set a valid entry time." });
  if (!Number.isFinite(exitAt) || (Number.isFinite(entryAt) && exitAt <= entryAt)) {
    issues.push({ field: "exitAt", message: "The exit time must be after the entry time." });
  }
  if (!strategy.legs.length) issues.push({ field: "legs", message: "Add at least one option leg." });

  strategy.legs.forEach((leg, index) => {
    const position = `Leg ${index + 1}`;
    const add = (field: string, message: string) => issues.push({ field: `leg.${leg.id}.${field}`, legId: leg.id, message: `${position}: ${message}` });
    if (strategy.lotsMode === "manual" && (!Number.isFinite(leg.lots) || leg.lots < 1)) add("lots", "lots must be at least 1.");
    if (!leg.expiry || Number.isNaN(new Date(`${leg.expiry}T00:00:00`).getTime())) add("expiry", "choose an expiry date.");
    if (leg.strikeMode === "exact" && (!leg.exactStrike || leg.exactStrike <= 0)) add("exactStrike", "enter the exact strike.");
    if (leg.orderType === "limit_order" && (!leg.limitPrice || !/^\d+(\.\d+)?$/.test(leg.limitPrice))) add("limitPrice", "enter a numeric limit price.");
    if (strategy.riskMode === "legwise" && leg.position === "sell" && (!leg.stopLoss || leg.stopLoss <= 0)) {
      add("stopLoss", "short legs need a stop loss in per-leg mode.");
    }
  });

  if (strategy.riskMode === "combined_premium") {
    if (!strategy.combinedStopLossPercent || strategy.combinedStopLossPercent <= 0 || strategy.combinedStopLossPercent > 100) {
      issues.push({ field: "combinedStopLossPercent", message: "Set the combined stop-loss percentage." });
    }
    if (strategy.legs.filter(leg => leg.position === "sell").length < 2) {
      issues.push({ field: "riskMode", message: "Combined-premium risk needs at least two short legs." });
    }
  }
  if (strategy.stopLossPercent <= 0 || strategy.stopLossPercent > 100) {
    issues.push({ field: "stopLossPercent", message: "Stop loss must be between 1% and 100%." });
  }
  if (strategy.takeProfitPercent <= 0) {
    issues.push({ field: "takeProfitPercent", message: "Take profit must be greater than zero." });
  }
  if (strategy.holdingMode === "hold_to_expiry" && strategy.exitMinutesBeforeExpiry < 1) {
    issues.push({ field: "exitMinutesBeforeExpiry", message: "Set an expiry safety buffer of at least one minute." });
  }
  if (strategy.sameExpiryRequired && new Set(strategy.legs.map(leg => leg.expiry)).size > 1) {
    issues.push({ field: "legs", message: "Every leg must use the same fallback expiry date." });
  }
  return issues;
}

/* ------------------------------------------------------------------ *
 * Pre-flight checklist
 * ------------------------------------------------------------------ */

type ChecklistRow = { id: string; label: string; passed: boolean; detail: string };

/**
 * The same blocking issues, grouped into the four requirements a run has to
 * satisfy.
 *
 * The rail used to state one verdict — complete, or "N items need attention" —
 * which told the operator that something was wrong but not what stage it was at.
 * Grouping the identical validation output into named requirements means each
 * row can report its own outstanding problem, and a requirement draws its tick
 * the moment it is actually satisfied.
 */
function checklist(strategy: StrategyDefinition, issues: ValidationIssue[]): ChecklistRow[] {
  const first = (match: (field: string) => boolean, fallback: string) => {
    const found = issues.filter(issue => match(issue.field));
    if (!found.length) return fallback;
    return found.length === 1 ? found[0].message : `${found[0].message} (+${found.length - 1} more)`;
  };

  const named = !issues.some(issue => issue.field === "name");
  const scheduled = !issues.some(issue => issue.field === "entryAt" || issue.field === "exitAt");
  const legsOk = !issues.some(issue => issue.field === "legs" || issue.field.startsWith("leg."));
  const riskOk = !issues.some(issue => ["riskMode", "combinedStopLossPercent", "stopLossPercent", "takeProfitPercent"].includes(issue.field));

  return [
    {
      id: "named",
      label: "Named",
      passed: named,
      detail: named ? strategy.name.trim() : first(field => field === "name", "")
    },
    {
      id: "schedule",
      label: "Schedule window",
      passed: scheduled,
      detail: scheduled
        ? `${formatDateTime(strategy.entry.entryAt)} · held ${formatDuration(strategy.entry.entryAt, strategy.entry.exitAt)}`
        : first(field => field === "entryAt" || field === "exitAt", "")
    },
    {
      id: "legs",
      label: "Legs configured",
      passed: legsOk,
      detail: legsOk
        ? `${strategy.legs.length} ${strategy.legs.length === 1 ? "leg" : "legs"} fully specified`
        : first(field => field === "legs" || field.startsWith("leg."), "")
    },
    {
      id: "risk",
      label: "Risk control",
      passed: riskOk,
      detail: riskOk
        ? strategy.riskMode === "combined_premium"
          ? `Combined stop at ${strategy.combinedStopLossPercent ?? 0}% of credit`
          : strategy.riskMode === "strategy_level"
            ? `${strategy.stopLossPercent}% stop · ${strategy.takeProfitPercent}% take profit`
            : "Stops held inside each leg"
        : first(field => ["riskMode", "combinedStopLossPercent", "stopLossPercent", "takeProfitPercent"].includes(field), "")
    }
  ];
}

/* ------------------------------------------------------------------ *
 * Derived structure model, used by the review visualisations
 * ------------------------------------------------------------------ */

/**
 * Signed distance from at-the-money, measured in strike steps.
 *
 * A call moves out of the money as the strike rises, a put as the strike falls,
 * so both are mapped onto one axis where positive means "above spot". This is
 * what turns a list of legs into a recognisable structure: a straddle collapses
 * to a single point, a strangle spreads symmetrically, a spread sits on one side.
 */
function strikeOffset(leg: StrategyLeg) {
  if (leg.strikeMode === "exact") return null;
  if (leg.strikeMode === "atm") return 0;
  const above = leg.optionType === "call" ? leg.strikeMode === "otm" : leg.strikeMode === "itm";
  return (above ? 1 : -1) * Math.abs(leg.strikeSteps || 0);
}

type StructureModel = {
  relative: { leg: StrategyLeg; offset: number }[];
  fixed: StrategyLeg[];
  span: number;
  shortLots: number;
  longLots: number;
};

function structureModel(legs: StrategyLeg[]): StructureModel {
  const relative: { leg: StrategyLeg; offset: number }[] = [];
  const fixed: StrategyLeg[] = [];
  for (const leg of legs) {
    const offset = strikeOffset(leg);
    if (offset === null) fixed.push(leg);
    else relative.push({ leg, offset });
  }
  const span = Math.max(1, ...relative.map(item => Math.abs(item.offset)));
  return {
    relative,
    fixed,
    span,
    shortLots: legs.filter(leg => leg.position === "sell").reduce((total, leg) => total + leg.lots, 0),
    longLots: legs.filter(leg => leg.position === "buy").reduce((total, leg) => total + leg.lots, 0)
  };
}

/* ------------------------------------------------------------------ *
 * Builder
 * ------------------------------------------------------------------ */

type LibraryState = "loading" | "template" | "local" | "unsaved" | "saving" | "saved" | "error";

const LIBRARY_COPY: Record<LibraryState, { label: string; tone: "active" | "warning" | "negative" | "neutral" }> = {
  loading: { label: "Loading saved strategies", tone: "neutral" },
  template: { label: "Built-in strategy", tone: "neutral" },
  local: { label: "Unsaved draft", tone: "warning" },
  unsaved: { label: "Unsaved changes", tone: "warning" },
  saving: { label: "Saving", tone: "warning" },
  saved: { label: "Saved", tone: "active" },
  error: { label: "Saved strategies unavailable", tone: "negative" }
};

export default function StrategyBuilder({ userId, onNotice, liveEnabled }: {
  userId: string;
  onNotice: NoticeHandler;
  /** False while the trading backend is unreachable: design and export only. */
  liveEnabled: boolean;
}) {
  const [strategy, setStrategy] = useState<StrategyDefinition>(initialStrategy);
  const [expandedLeg, setExpandedLeg] = useState<string | null>(strategy.legs[0]?.id ?? null);
  const [showIssues, setShowIssues] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [error, setError] = useState("");
  const [draftReady, setDraftReady] = useState(false);
  const [libraryReady, setLibraryReady] = useState(false);
  const [savedStrategies, setSavedStrategies] = useState<SavedStrategy[]>([]);
  const [activeSavedId, setActiveSavedId] = useState<string | null>(null);
  const [savedFingerprint, setSavedFingerprint] = useState("");
  const [libraryState, setLibraryState] = useState<LibraryState>("loading");
  const [confirmNew, setConfirmNew] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [descriptionOpen, setDescriptionOpen] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);

  const [scheduled, setScheduled] = useState(false);

  const issues = useMemo(() => validate(strategy), [strategy]);
  const invalidFields = useMemo(
    () => (showIssues ? new Set(issues.map(issue => issue.field)) : new Set<string>()),
    [issues, showIssues]
  );
  const structure = useMemo(() => structureModel(strategy.legs), [strategy.legs]);
  const requirements = useMemo(() => checklist(strategy, issues), [issues, strategy]);

  /** A rejected submission shakes the block that reported it. */
  const { target: reviewPanel, shake } = useShake<HTMLDivElement>();

  /**
   * Saving flips a real boolean — the definition either matches the stored row
   * or it does not — so the moment it lands gets a fill, a pop and a short
   * burst. Editing again only drops the fill: the celebration belongs to the way
   * in.
   *
   * `hasSaved` gates it so the very first library load, which arrives already
   * saved, does not celebrate something the operator did not do. It is the same
   * reason the toggle recipe withholds its bounce until first interaction.
   */
  const [hasSaved, setHasSaved] = useState(false);
  const sawSaving = useRef(false);
  useEffect(() => {
    if (libraryState === "saving") {
      sawSaving.current = true;
      return;
    }
    if (libraryState === "saved" && sawSaving.current) {
      sawSaving.current = false;
      setHasSaved(true);
    }
  }, [libraryState]);

  const savedToLibrary = libraryState === "saved";
  const { hostRef: saveButton, particles: saveParticles } = useBurst(hasSaved && savedToLibrary);

  /* --------------------------- persistence --------------------------- */

  const persist = useCallback(async (
    definition: StrategyDefinition,
    savedId: string | null,
    notify = false
  ): Promise<SavedStrategy | null> => {
    const trimmedName = definition.name.trim();
    if (trimmedName.length < 2) {
      setShowIssues(true);
      setError("Enter a strategy name with at least two characters before saving.");
      setLibraryState("unsaved");
      return null;
    }
    const normalized = { ...definition, name: trimmedName };
    setLibraryState("saving");
    try {
      const supabase = getSupabaseBrowserClient();
      const columns = "id,user_id,name,definition_json,source_run_id,version,enabled_for_ai,created_at,updated_at";
      const existing = savedStrategies.find(item => item.id === savedId);
      const { data, error: saveError } = savedId && !existing?.isDefault
        ? await supabase.from("saved_strategies")
          .update({ name: trimmedName, definition_json: normalized as unknown as Json, enabled_for_ai: normalized.enabledForAi })
          .eq("id", savedId).eq("user_id", userId).select(columns).single()
        : await supabase.from("saved_strategies")
          .insert({ user_id: userId, name: trimmedName, definition_json: normalized as unknown as Json, enabled_for_ai: normalized.enabledForAi })
          .select(columns).single();
      if (saveError) throw saveError;

      const saved = savedStrategyFromRow(data as SavedStrategyRow);
      if (!saved) throw new Error("Supabase returned an invalid saved strategy.");

      setSavedStrategies(current => [saved, ...current.filter(item => item.id !== saved.id)]
        .sort(sortSavedStrategies));
      setActiveSavedId(saved.id);
      setSavedFingerprint(fingerprint(saved.definition));
      setLibraryState("saved");
      setError("");
      if (trimmedName !== definition.name) setStrategy(normalized);
      if (notify) onNotice({ tone: "ok", text: `${trimmedName} saved to your strategy library.` });
      return saved;
    } catch (saveError) {
      const message = `Could not save the strategy library: ${errorMessage(saveError)}`;
      setLibraryState("error");
      setError(message);
      if (notify) onNotice({ tone: "error", text: message });
      return null;
    }
  }, [onNotice, savedStrategies, userId]);

  // Browser recovery copy, restored before the Supabase library loads.
  useEffect(() => {
    try {
      const cached = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached) as unknown;
        if (isStrategyDefinition(parsed)) {
          setStrategy(hydrateStrategy(parsed));
          setExpandedLeg(parsed.legs[0]?.id ?? null);
        }
      }
    } catch {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
    } finally {
      setDraftReady(true);
    }
  }, []);

  useEffect(() => {
    if (draftReady) localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(strategy));
  }, [draftReady, strategy]);

  useEffect(() => {
    if (!draftReady) return;
    let cancelled = false;

    async function loadLibrary() {
      setLibraryState("loading");
      try {
        const pageSize = 500;
        const rows: SavedStrategyRow[] = [];
        for (let from = 0; ; from += pageSize) {
          const { data, error: loadError } = await getSupabaseBrowserClient()
            .from("saved_strategies")
            .select("id,user_id,name,definition_json,source_run_id,version,enabled_for_ai,created_at,updated_at")
            .order("updated_at", { ascending: false })
            .range(from, from + pageSize - 1);
          if (loadError) throw loadError;
          const page = (data ?? []) as SavedStrategyRow[];
          rows.push(...page);
          if (page.length < pageSize) break;
        }
        if (cancelled) return;

        const parsed = rows
          .map(savedStrategyFromRow)
          .filter((item): item is SavedStrategy => item !== null);
        const library = markLegacyDefaultCopies(parsed).sort(sortSavedStrategies);
        setSavedStrategies(library);

        const cachedId = localStorage.getItem(DRAFT_ID_STORAGE_KEY);
        const selected = library.find(item => item.id === cachedId) ?? library[0];
        if (!selected) {
          setLibraryState("local");
          return;
        }

        // Prefer the unsaved recovery copy when it belongs to the selected row.
        let cachedDefinition: StrategyDefinition | null = null;
        const cached = localStorage.getItem(DRAFT_STORAGE_KEY);
        if (cachedId === selected.id && cached) {
          try {
            const parsed = JSON.parse(cached) as unknown;
            if (isStrategyDefinition(parsed)) cachedDefinition = hydrateStrategy(parsed);
          } catch { /* the recovery effect above already cleared invalid cache data */ }
        }

        const definition = refreshExpiredSchedule(cachedDefinition ?? selected.definition);
        setActiveSavedId(selected.id);
        setStrategy(definition);
        setExpandedLeg(definition.legs[0]?.id ?? null);
        setSavedFingerprint(fingerprint(selected.definition));
        setLibraryState(selected.isDefault
          ? "template"
          : fingerprint(definition) === fingerprint(selected.definition) ? "saved" : "unsaved");
      } catch (loadError) {
        if (cancelled) return;
        setLibraryState("error");
        onNotice({ tone: "error", text: errorMessage(loadError, "Saved strategies are temporarily unavailable. Please try again.") });
      } finally {
        if (!cancelled) setLibraryReady(true);
      }
    }

    void loadLibrary();
    return () => { cancelled = true; };
  }, [draftReady, onNotice, userId]);

  useEffect(() => {
    if (!draftReady) return;
    if (activeSavedId) localStorage.setItem(DRAFT_ID_STORAGE_KEY, activeSavedId);
    else localStorage.removeItem(DRAFT_ID_STORAGE_KEY);
  }, [activeSavedId, draftReady]);

  // Debounced autosave of the selected definition.
  useEffect(() => {
    if (!libraryReady || !activeSavedId) return;
    if (savedStrategies.find(item => item.id === activeSavedId)?.isDefault) {
      setLibraryState("template");
      return;
    }
    if (fingerprint(strategy) === savedFingerprint) {
      setLibraryState("saved");
      return;
    }
    setLibraryState("unsaved");
    if (strategy.name.trim().length < 2) return;
    const timer = window.setTimeout(() => { void persist(strategy, activeSavedId); }, 900);
    return () => window.clearTimeout(timer);
  }, [activeSavedId, libraryReady, persist, savedFingerprint, savedStrategies, strategy]);

  /* ----------------------------- legs ----------------------------- */

  const updateLeg = (id: string, patch: Partial<StrategyLeg>) =>
    setStrategy(current => ({ ...current, legs: current.legs.map(leg => leg.id === id ? { ...leg, ...patch } : leg) }));

  const removeLeg = (id: string) =>
    setStrategy(current => ({ ...current, legs: current.legs.filter(leg => leg.id !== id) }));

  const duplicateLeg = (id: string) =>
    setStrategy(current => current.legs.length >= MAX_LEGS
      ? current
      : { ...current, legs: current.legs.flatMap(leg => leg.id === id ? [leg, { ...leg, id: uid() }] : [leg]) });

  const moveLeg = (index: number, direction: -1 | 1) =>
    setStrategy(current => {
      const legs = [...current.legs];
      const target = index + direction;
      if (target < 0 || target >= legs.length) return current;
      [legs[index], legs[target]] = [legs[target], legs[index]];
      return { ...current, legs };
    });

  function addLeg(overrides: Partial<StrategyLeg> = {}) {
    if (strategy.legs.length >= MAX_LEGS) return;
    const leg = newLeg({ expiry: strategy.legs[0]?.expiry || tomorrow(), ...overrides });
    setStrategy(current => ({ ...current, legs: [...current.legs, leg] }));
    setExpandedLeg(leg.id);
  }

  /**
   * Copies the last leg's expiry, strike criteria and lot size and flips only
   * what the preset names. Adding the matching side of a spread was previously
   * four separate edits after the leg appeared.
   */
  function addMatchingLeg(patch: Partial<StrategyLeg>) {
    const last = strategy.legs[strategy.legs.length - 1];
    addLeg(last
      ? {
        expiry: last.expiry,
        lots: last.lots,
        strikeMode: last.strikeMode,
        strikeSteps: last.strikeSteps,
        exactStrike: last.exactStrike,
        orderType: last.orderType,
        position: last.position,
        optionType: last.optionType,
        ...patch
      }
      : patch);
  }

  /* --------------------------- commands --------------------------- */

  async function startNewStrategy() {
    if (activeSavedId && !activeSaved?.isDefault && fingerprint(strategy) !== savedFingerprint) {
      if (!await persist(strategy, activeSavedId)) return;
    }
    const fresh = initialStrategy();
    const saved = await persist(fresh, null);
    if (!saved) return;
    setStrategy(fresh);
    setExpandedLeg(fresh.legs[0]?.id ?? null);
    setShowIssues(false);
    setError("");
    setConfirmNew(false);
    onNotice({ tone: "ok", text: "New strategy created and saved. Strategy names can be reused." });
  }

  async function switchStrategy(savedId: string) {
    if (savedId === activeSavedId) return;
    if (activeSavedId && !activeSaved?.isDefault && fingerprint(strategy) !== savedFingerprint) {
      if (!await persist(strategy, activeSavedId)) return;
    } else if (!activeSavedId && strategy.name.trim().length >= 2) {
      if (!await persist(strategy, null)) return;
    }
    const selected = savedStrategies.find(item => item.id === savedId);
    if (!selected) return;
    const definition = refreshExpiredSchedule(selected.definition);
    setActiveSavedId(selected.id);
    setStrategy(definition);
    setExpandedLeg(definition.legs[0]?.id ?? null);
    setShowIssues(false);
    setError("");
    setSavedFingerprint(fingerprint(selected.definition));
    setLibraryState(selected.isDefault
      ? "template"
      : fingerprint(definition) === fingerprint(selected.definition) ? "saved" : "unsaved");
  }

  async function deleteStrategy() {
    if (!activeSavedId || activeSaved?.isDefault) return;
    const deletedId = activeSavedId;
    setLibraryState("saving");
    try {
      const { error: deleteError } = await getSupabaseBrowserClient()
        .from("saved_strategies").delete().eq("id", deletedId).eq("user_id", userId);
      if (deleteError) throw deleteError;

      const remaining = savedStrategies.filter(item => item.id !== deletedId);
      setSavedStrategies(remaining);
      const next = remaining[0];
      if (next) {
        const definition = refreshExpiredSchedule(next.definition);
        setActiveSavedId(next.id);
        setStrategy(definition);
        setExpandedLeg(definition.legs[0]?.id ?? null);
        setSavedFingerprint(fingerprint(next.definition));
        setLibraryState(fingerprint(definition) === fingerprint(next.definition) ? "saved" : "unsaved");
      } else {
        const fresh = initialStrategy();
        setActiveSavedId(null);
        setStrategy(fresh);
        setExpandedLeg(fresh.legs[0]?.id ?? null);
        setSavedFingerprint("");
        setLibraryState("local");
      }
      setConfirmDelete(false);
      setShowIssues(false);
      setError("");
      onNotice({ tone: "ok", text: "Saved strategy deleted. Its strategy history was not changed." });
    } catch (deleteError) {
      const message = `Could not delete the saved strategy: ${errorMessage(deleteError)}`;
      setLibraryState("error");
      setError(message);
      onNotice({ tone: "error", text: message });
    }
  }

  async function scheduleStrategy() {
    if (!liveEnabled) {
      setError("Live trading is currently unavailable. Please try again shortly.");
      return;
    }
    if (issues.length) {
      setShowIssues(true);
      setError(`Resolve ${issues.length} ${issues.length === 1 ? "problem" : "problems"} before scheduling.`);
      shake();
      const firstLeg = issues.find(issue => issue.legId)?.legId;
      if (firstLeg) setExpandedLeg(firstLeg);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const target = document.querySelector<HTMLElement>(".field.invalid, .segmented-field.invalid, .risk-input.invalid, .leg.invalid");
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
        target?.querySelector<HTMLElement>("input, select, button")?.focus();
      }));
      return;
    }
    setScheduling(true);
    setError("");
    setShowIssues(false);
    try {
      const saved = activeSaved?.isDefault
        ? { ...activeSaved, definition: strategy }
        : await persist(strategy, activeSavedId, false);
      if (!saved) return;
      const liveStrategy = { ...saved.definition, acknowledgement: true as const };
      await requestJson<{ result: { id: string } }>("/api/strategies", {
        method: "POST",
        body: JSON.stringify({ strategy: liveStrategy, status: "scheduled", savedStrategyId: saved.id })
      });
      // An immutable run now exists on the server. That is worth confirming on
      // the control that created it, not only in a toast that will time out.
      setScheduled(true);
      window.setTimeout(() => setScheduled(false), 2_600);
      onNotice({
        tone: "ok",
        text: `Scheduled for ${formatDateTime(liveStrategy.entry.entryAt)}. No order is placed before that time.`
      });
    } catch (scheduleError) {
      setError(errorMessage(scheduleError));
      shake();
    } finally {
      setScheduling(false);
    }
  }

  function exportDraft() {
    const payload = JSON.stringify({ version: 2, exportedAt: new Date().toISOString(), strategy }, null, 2);
    const href = URL.createObjectURL(new Blob([payload], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${strategy.name.trim().replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase() || "delta-strategy"}.json`;
    anchor.click();
    URL.revokeObjectURL(href);
    onNotice({ tone: "ok", text: "Strategy backup downloaded." });
  }

  async function importDraft(file?: File) {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as unknown;
      const candidate = parsed && typeof parsed === "object" && "strategy" in parsed
        ? (parsed as { strategy: unknown }).strategy
        : parsed;
      if (!isStrategyDefinition(candidate)) throw new Error("This file is not a valid Delta strategy draft.");
      const imported = hydrateStrategy(candidate);
      setActiveSavedId(null);
      setSavedFingerprint("");
      setLibraryState("local");
      setStrategy(imported);
      setExpandedLeg(imported.legs[0]?.id ?? null);
      setShowIssues(false);
      setError("");
      onNotice({ tone: "ok", text: "Imported as a new draft. Choose Save to add it to your saved strategies." });
    } catch (importError) {
      onNotice({ tone: "error", text: errorMessage(importError) });
    } finally {
      if (importInput.current) importInput.current.value = "";
    }
  }

  /* ----------------------------- render ----------------------------- */

  const activeSaved = savedStrategies.find(item => item.id === activeSavedId) ?? null;
  const library = LIBRARY_COPY[libraryState];
  const busy = libraryState === "loading" || libraryState === "saving";

  return (
    <div className="builder">
      <SectionHeading
        eyebrow="Options strategy"
        title="Strategy builder"
        description="Build a reusable options strategy, then choose when it should enter and exit."
        actions={
          <>
            <button type="button" className="button ghost" onClick={() => importInput.current?.click()}>
              <Upload aria-hidden="true" />Import
            </button>
            <button type="button" className="button secondary" onClick={exportDraft}>
              <Download aria-hidden="true" />Export
            </button>
            <input
              ref={importInput}
              className="visually-hidden"
              type="file"
              accept="application/json,.json"
              aria-label="Import a strategy backup file"
              onChange={event => void importDraft(event.target.files?.[0])}
            />
          </>
        }
      />

      <div className="builder-columns">
        <div className="builder-main">
          <Panel>
            <PanelHeader
              icon={<CircleDollarSign />}
              title="Strategy details"
              meta="Name, market, and strategy type"
            />
            <div className="grid-2">
              <Field label="Strategy name" hint="Names may be reused; every schedule is a separate run." invalid={invalidFields.has("name")}>
                <ClearableInput
                  value={strategy.name}
                  maxLength={80}
                  placeholder="Name this strategy"
                  clearLabel="Clear the strategy name"
                  onChange={name => setStrategy({ ...strategy, name })}
                />
              </Field>
              <Field label="Description" hint="Included when the automation agent compares strategies.">
                <button
                  type="button"
                  className="description-trigger"
                  aria-haspopup="dialog"
                  onClick={() => setDescriptionOpen(true)}
                >
                  <FileText aria-hidden="true" />
                  <span>{strategy.description || "Add a strategy description"}</span>
                  <span className="description-trigger-action"><Maximize2 aria-hidden="true" />Open</span>
                </button>
              </Field>
              <Select
                label="Category"
                value={strategy.category}
                onChange={value => setStrategy({ ...strategy, category: value as StrategyDefinition["category"] })}
                options={[
                  { value: "premium_buying", label: "Premium buying" },
                  { value: "premium_selling", label: "Premium selling" },
                  { value: "defined_risk_premium_selling", label: "Defined-risk premium selling" }
                ]}
              />
              <Select
                label="Market outlook"
                value={strategy.marketOutlook}
                onChange={value => setStrategy({ ...strategy, marketOutlook: value as StrategyDefinition["marketOutlook"] })}
                options={[
                  { value: "bullish", label: "Bullish" },
                  { value: "bearish", label: "Bearish" },
                  { value: "large_move_unknown_direction", label: "Large move, direction unknown" },
                  { value: "very_large_move_unknown_direction", label: "Very large move, direction unknown" },
                  { value: "sideways", label: "Tight sideways" },
                  { value: "wide_sideways", label: "Wide sideways" },
                  { value: "tight_sideways", label: "ATM pinning" }
                ]}
              />
              <Select
                label="Index"
                value={strategy.instrument.index}
                onChange={value => setStrategy({
                  ...strategy,
                  instrument: {
                    ...strategy.instrument,
                    index: value as "BTCUSD" | "ETHUSD",
                    underlying: value === "BTCUSD" ? "BTC" : "ETH"
                  }
                })}
                options={[{ value: "BTCUSD", label: "BTCUSD" }, { value: "ETHUSD", label: "ETHUSD" }]}
              />
              <Segmented
                label="Price source"
                value={strategy.instrument.underlyingFrom}
                onChange={value => setStrategy({
                  ...strategy,
                  instrument: { ...strategy.instrument, underlyingFrom: value as "cash" | "futures" }
                })}
                options={[{ value: "cash", label: "Cash" }, { value: "futures", label: "Futures" }]}
              />
              <Segmented
                label="Strategy type"
                value={strategy.entry.strategyType}
                onChange={value => setStrategy({
                  ...strategy,
                  entry: { ...strategy.entry, strategyType: value as "intraday" | "btst" | "positional" }
                })}
                options={[
                  { value: "intraday", label: "Intraday" },
                  { value: "btst", label: "BTST, overnight" },
                  { value: "positional", label: "Positional" }
                ]}
              />
              <Toggle
                label="Available to automation"
                description="The agent may select this saved version. It cannot edit it."
                checked={strategy.enabledForAi}
                onChange={enabledForAi => setStrategy({ ...strategy, enabledForAi })}
              />
            </div>
          </Panel>

          <Panel>
            <PanelHeader icon={<CalendarClock />} title="Schedule" meta="Local time on this machine" />
            <div className="grid-2">
              <Segmented
                label="Holding mode"
                value={strategy.holdingMode}
                onChange={value => setStrategy({ ...strategy, holdingMode: value as StrategyDefinition["holdingMode"] })}
                options={[
                  { value: "intraday", label: "Intraday" },
                  { value: "hold_to_expiry", label: "Hold to expiry" }
                ]}
              />
              <Select
                label="Expiry policy"
                value={strategy.expiryPolicy}
                onChange={value => setStrategy({ ...strategy, expiryPolicy: value as StrategyDefinition["expiryPolicy"] })}
                options={[
                  { value: "same_day", label: "Same day" },
                  { value: "next_day", label: "Next listed day" },
                  { value: "7_day", label: "Closest listed expiry after 7 days" },
                  { value: "30_day", label: "Closest listed expiry after 30 days" }
                ]}
              />
              <Field label="Entry time" invalid={invalidFields.has("entryAt")}>
                <input
                  type="datetime-local"
                  value={toLocalInput(strategy.entry.entryAt)}
                  onChange={event => setStrategy({ ...strategy, entry: { ...strategy.entry, entryAt: toIso(event.target.value) } })}
                />
              </Field>
              <Field label={strategy.holdingMode === "intraday" ? "Exit time" : "Fallback exit time"} invalid={invalidFields.has("exitAt")}>
                <input
                  type="datetime-local"
                  value={toLocalInput(strategy.entry.exitAt)}
                  onChange={event => setStrategy({ ...strategy, entry: { ...strategy.entry, exitAt: toIso(event.target.value) } })}
                />
              </Field>
              {strategy.holdingMode === "hold_to_expiry" && (
                <NumberField
                  label="Exit minutes before expiry"
                  min={1}
                  max={1440}
                  value={strategy.exitMinutesBeforeExpiry}
                  invalid={invalidFields.has("exitMinutesBeforeExpiry")}
                  onChange={exitMinutesBeforeExpiry => setStrategy({ ...strategy, exitMinutesBeforeExpiry })}
                />
              )}
            </div>
            <ScheduleTimeline entryAt={strategy.entry.entryAt} exitAt={strategy.entry.exitAt} />
          </Panel>

          <Panel>
            <PanelHeader
              icon={<Shield />}
              title="Risk control"
              meta={strategy.riskMode === "legwise" ? "Each leg closes independently" : "The complete strategy closes together"}
            />
            <RiskControl strategy={strategy} onChange={setStrategy} invalidFields={invalidFields} />
          </Panel>

          <Panel>
            <PanelHeader
              icon={<Layers3 />}
              title="Position sizing"
              meta="Lot behavior for this strategy structure"
            />
            <div className="grid-3">
              <Segmented
                label="Lots"
                value={strategy.lotsMode}
                onChange={value => setStrategy({ ...strategy, lotsMode: value as StrategyDefinition["lotsMode"] })}
                options={[{ value: "auto", label: "Automatic" }, { value: "manual", label: "Manual" }]}
              />
              <OptionalNumberField
                label="Maximum lots"
                value={strategy.maximumLots}
                onChange={maximumLots => setStrategy({ ...strategy, maximumLots })}
                placeholder="No extra cap"
              />
              <Toggle
                label="Equal lots on every leg"
                checked={strategy.equalLotsRequired}
                onChange={equalLotsRequired => setStrategy({ ...strategy, equalLotsRequired })}
              />
              <Toggle
                label="Require one expiry"
                checked={strategy.sameExpiryRequired}
                onChange={sameExpiryRequired => setStrategy({ ...strategy, sameExpiryRequired })}
              />
            </div>
          </Panel>

          <Panel className={invalidFields.has("legs") ? "invalid" : ""}>
            <PanelHeader
              icon={<Layers3 />}
              title="Legs"
              meta={`${strategy.legs.length} of ${MAX_LEGS} · ${structure.shortLots} short, ${structure.longLots} long lots`}
              actions={
                /*
                  The control becomes the surface it opens rather than popping a
                  separate panel beside it, because the trigger and the menu are
                  the same element. "Add leg" is still the first item, so nothing
                  that worked by clicking straight through it stopped working.
                */
                <div className="leg-add-slot">
                  <MorphMenu
                    className="leg-add"
                    label="Add leg"
                    icon={<Plus aria-hidden="true" />}
                    disabled={strategy.legs.length >= MAX_LEGS}
                  >
                    <p className="leg-add-heading">Add a leg</p>
                    <button type="button" role="menuitem" className="leg-add-menu-item" onClick={() => addLeg()}>
                      <Plus aria-hidden="true" />Blank leg
                    </button>
                    <button type="button" role="menuitem" className="leg-add-menu-item" onClick={() => addMatchingLeg({ optionType: "call" })}>
                      <CircleDollarSign aria-hidden="true" />Matching call
                    </button>
                    <button type="button" role="menuitem" className="leg-add-menu-item" onClick={() => addMatchingLeg({ optionType: "put" })}>
                      <CircleDollarSign aria-hidden="true" />Matching put
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className="leg-add-menu-item"
                      onClick={() => addMatchingLeg({ position: strategy.legs[strategy.legs.length - 1]?.position === "buy" ? "sell" : "buy" })}
                    >
                      <Copy aria-hidden="true" />Opposite side
                    </button>
                  </MorphMenu>
                </div>
              }
            />
            {strategy.legs.length ? (
              <ol className="leg-list">
                {strategy.legs.map((leg, index) => (
                  <LegRow
                    key={leg.id}
                    leg={leg}
                    index={index}
                    total={strategy.legs.length}
                    riskMode={strategy.riskMode}
                    open={expandedLeg === leg.id}
                    invalidFields={invalidFields}
                    onToggle={() => setExpandedLeg(expandedLeg === leg.id ? null : leg.id)}
                    onUpdate={patch => updateLeg(leg.id, patch)}
                    onRemove={() => removeLeg(leg.id)}
                    onDuplicate={() => duplicateLeg(leg.id)}
                    onMove={direction => moveLeg(index, direction)}
                  />
                ))}
              </ol>
            ) : (
              <EmptyState
                icon={<Layers3 />}
                title="No legs configured"
                description="A strategy needs at least one option leg before it can be scheduled."
                action={<button type="button" className="button secondary" onClick={() => addLeg()}><Plus aria-hidden="true" />Add first leg</button>}
              />
            )}
          </Panel>
        </div>

        <aside className="builder-rail">
          <Panel>
            <PanelHeader
              icon={<FolderOpen />}
              title="Strategy library"
              meta={`${savedStrategies.filter(item => item.isDefault).length} built-in · ${savedStrategies.filter(item => !item.isDefault).length} custom`}
            />
            <Field label="Current strategy">
              <span className="select-wrap">
                <select
                  value={activeSavedId ?? ""}
                  disabled={busy}
                  onChange={event => void switchStrategy(event.target.value)}
                >
                  {!activeSavedId && <option value="">New unsaved strategy</option>}
                  <optgroup label="Built-in strategies">
                    {savedStrategies.filter(item => item.isDefault).map(item => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </optgroup>
                  {savedStrategies.some(item => !item.isDefault) && (
                    <optgroup label="Your strategies">
                      {savedStrategies.filter(item => !item.isDefault).map(item => (
                        <option key={item.id} value={item.id}>{item.name}</option>
                      ))}
                    </optgroup>
                  )}
                </select>
                <ChevronDown aria-hidden="true" />
              </span>
            </Field>
            {/* One status changing, not two strings replacing each other. */}
            <p className={`library-state tone-${library.tone}`}>
              <StatusDot tone={library.tone} />
              <SwapText>{library.label}</SwapText>
              <small>{activeSaved?.isDefault ? "Shared default" : "Draft recovery on"}</small>
            </p>
            <div className="button-row">
              <button
                type="button"
                className={`button secondary save-control t-like${hasSaved ? " is-init" : ""}`}
                data-liked={savedToLibrary}
                ref={saveButton}
                onClick={() => void persist(strategy, activeSavedId, true)}
                disabled={busy}
              >
                <span className="t-like-icon" aria-hidden="true"><Save /></span>
                {saveParticles}
                <SwapText>{libraryState === "saving"
                  ? "Saving"
                  : activeSaved?.isDefault
                    ? "Save a copy"
                    : libraryState === "saved" ? "Saved" : "Save"}</SwapText>
              </button>
              <button type="button" className="button primary" onClick={() => setConfirmNew(true)} disabled={busy || scheduling}>
                <Plus aria-hidden="true" />New
              </button>
              <button
                type="button"
                className="button ghost icon-only"
                onClick={() => setConfirmDelete(true)}
                disabled={!activeSaved || activeSaved.isDefault || busy}
                aria-label="Delete saved strategy"
                title={activeSaved?.isDefault ? "Built-in strategies cannot be deleted" : "Delete saved strategy"}
              >
                <Trash2 aria-hidden="true" />
              </button>
            </div>
          </Panel>

          <Panel>
            <PanelHeader icon={<ShieldCheck />} title="Review" meta="Checked before scheduling" />

            <StructureStrip structure={structure} />

            <dl className="review-facts">
              <div><dt>Instrument</dt><dd>{strategy.instrument.index} · {strategy.instrument.underlyingFrom}</dd></div>
              <div><dt>Legs</dt><dd>{strategy.legs.length} ({structure.shortLots} short / {structure.longLots} long lots)</dd></div>
              <div><dt>Entry</dt><dd>{formatDateTime(strategy.entry.entryAt)} <small>{relativeTime(strategy.entry.entryAt)}</small></dd></div>
              <div><dt>Hold</dt><dd>{formatDuration(strategy.entry.entryAt, strategy.entry.exitAt)}</dd></div>
              <div><dt>Lots</dt><dd>{strategy.lotsMode === "auto" ? "Automatic from account capital policy" : "Manual"}</dd></div>
              <div>
                <dt>Risk</dt>
                <dd>{strategy.riskMode === "legwise"
                  ? `Per leg · ${strategy.squareOff} square off`
                  : `${strategy.riskBasis.replaceAll("_", " ")} · ${strategy.stopLossPercent}% stop · ${strategy.takeProfitPercent}% target`}</dd>
              </div>
            </dl>

            {/* `t-input` marks the element the shake acts on: a rejected
                submission recoils the block that reported why. */}
            <div className="review-verdict t-input" ref={reviewPanel}>
              <ul className="review-checklist">
                {requirements.map(row => (
                  <li className="review-check" key={row.id} data-checked={row.passed}>
                    {/* The box fills, then the tick draws itself. Animating the
                        dash offset rather than swapping it means a requirement
                        that lapses reverses cleanly instead of blinking off. */}
                    <span className="review-check-box t-check" data-checked={row.passed} aria-hidden="true">
                      <DrawnTick />
                    </span>
                    <span className="review-check-label">
                      <strong>{row.label}</strong>
                      {row.detail}
                    </span>
                    <span className="visually-hidden">{row.passed ? "Requirement met" : "Outstanding"}</span>
                  </li>
                ))}
              </ul>

              {error && <InlineMessage tone="error">{error}</InlineMessage>}

              {/*
                The beam is an active-state treatment, not decoration: it only
                runs while the configuration actually validates and the backend
                is reachable, so it marks the control as armed.
              */}
              <BorderBeam
                className="review-cta"
                size="sm"
                colorVariant="ocean"
                theme="dark"
                strength={0.5}
                active={issues.length === 0 && liveEnabled && !scheduling}
              >
                <button
                  type="button"
                  className={liveEnabled ? "button primary block" : "button secondary block"}
                  disabled={scheduling || !liveEnabled}
                  onClick={() => void scheduleStrategy()}
                >
                  {scheduling
                    ? <><LoaderCircle className="spin" aria-hidden="true" /><Shimmer>Scheduling</Shimmer></>
                    : scheduled
                      ? <><SuccessCheck shown size={18} />Strategy scheduled</>
                      : liveEnabled
                        ? <><CalendarClock aria-hidden="true" />Schedule strategy</>
                        : <><WifiOff aria-hidden="true" />Live trading unavailable</>}
                </button>
              </BorderBeam>
            </div>
            <p className="fine-print">
              Each schedule creates a separate record. Orders are submitted at the selected entry time.
            </p>
          </Panel>
        </aside>
      </div>

      {confirmNew && (
        <ConfirmModal
          tone="neutral"
          title="Create a new strategy?"
          description="The current strategy is kept. A new short-straddle strategy with fresh entry and exit times is added to your saved strategies."
          cancel="Keep editing"
          confirm="Create strategy"
          onClose={() => setConfirmNew(false)}
          onConfirm={() => void startNewStrategy()}
        />
      )}

      {confirmDelete && activeSaved && (
        <ConfirmModal
          title="Delete saved strategy?"
          description={`${activeSaved.name} will be removed from your reusable library. Scheduled, active, and historical runs remain unchanged.`}
          cancel="Keep strategy"
          confirm="Delete strategy"
          onClose={() => setConfirmDelete(false)}
          onConfirm={() => void deleteStrategy()}
        />
      )}
      {descriptionOpen && (
        <Dialog
          title="Strategy description"
          subtitle={strategy.name}
          aside={<span className="description-count">{strategy.description.length}/500</span>}
          size="compact"
          onClose={() => setDescriptionOpen(false)}
        >
          <Field label="Description" hint="The automation agent reads this when it compares saved strategies.">
            <textarea
              className="description-editor"
              value={strategy.description}
              maxLength={500}
              placeholder="Explain when this strategy should be used, what market it suits, and its main risk."
              onChange={event => setStrategy({ ...strategy, description: event.target.value })}
            />
          </Field>
        </Dialog>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Schedule timeline
 * ------------------------------------------------------------------ */

/**
 * Now → entry → exit on one axis. The gap before entry is the operator's
 * remaining edit window, so it is drawn to scale against the holding period
 * rather than described in prose.
 */
function ScheduleTimeline({ entryAt, exitAt }: { entryAt: string; exitAt: string }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const entry = new Date(entryAt).getTime();
  const exit = new Date(exitAt).getTime();
  if (now === null || !Number.isFinite(entry) || !Number.isFinite(exit) || exit <= entry) return null;

  const start = Math.min(now, entry);
  const total = Math.max(1, exit - start);
  const entryPercent = ((entry - start) / total) * 100;

  return (
    <div className="timeline">
      <div className="timeline-track" aria-hidden="true">
        <span className="timeline-wait" style={{ width: `${entryPercent}%` }} />
        <span className="timeline-hold" style={{ width: `${100 - entryPercent}%` }} />
        <i className="timeline-marker" style={{ left: `${entryPercent}%` }} />
      </div>
      <div className="timeline-legend">
        <span>
          <strong>{entry > now ? relativeTime(entryAt, now) : "Entry time has passed"}</strong>
          <small>until entry</small>
        </span>
        <span>
          <strong>{formatDuration(entryAt, exitAt)}</strong>
          <small>position held</small>
        </span>
        <span>
          <strong>{formatDateTime(exitAt)}</strong>
          <small>scheduled exit</small>
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Structure strip
 * ------------------------------------------------------------------ */

function StructureStrip({ structure }: { structure: StructureModel }) {
  if (!structure.relative.length && !structure.fixed.length) return null;

  return (
    <figure className="structure">
      <figcaption>Strike layout relative to at-the-money</figcaption>
      {structure.relative.length > 0 && (
        <div className="structure-axis">
          <span className="structure-atm" aria-hidden="true" />
          {structure.relative.map(({ leg, offset }) => (
            <span
              key={leg.id}
              className={`structure-leg ${leg.position} ${leg.optionType}`}
              style={{ left: `${50 + (offset / structure.span) * 42}%` }}
              title={`${leg.position} ${leg.optionType} · ${offset === 0 ? "ATM" : `${offset > 0 ? "+" : ""}${offset} steps`} · ${leg.lots} lots`}
            >
              {leg.optionType === "call" ? "C" : "P"}
            </span>
          ))}
          <span className="structure-scale">
            <small>below spot</small>
            <small>ATM</small>
            <small>above spot</small>
          </span>
        </div>
      )}
      {structure.fixed.length > 0 && (
        <p className="structure-fixed">
          {structure.fixed.length} {structure.fixed.length === 1 ? "leg uses" : "legs use"} an exact strike and
          {" "}{structure.fixed.length === 1 ? "is" : "are"} not placed on the relative axis.
        </p>
      )}
    </figure>
  );
}

/* ------------------------------------------------------------------ *
 * Risk control
 * ------------------------------------------------------------------ */

function RiskControl({ strategy, onChange, invalidFields }: {
  strategy: StrategyDefinition;
  onChange: (strategy: StrategyDefinition) => void;
  invalidFields: Set<string>;
}) {
  const strategyLevel = strategy.riskMode !== "legwise";
  const credit = strategy.riskBasis !== "net_debit";
  const stop = Math.min(100, strategy.stopLossPercent ?? strategy.combinedStopLossPercent ?? 100);
  const exitMultiple = credit ? 1 + stop / 100 : Math.max(0, 1 - stop / 100);

  return (
    /* Switching trigger swaps a four-cell grid for a three-column one, so the
       panel tweens between the two heights instead of snapping. */
    <div className="risk-control t-resize">
      <Select
        label="Risk basis"
        value={strategy.riskBasis}
        onChange={value => onChange({ ...strategy, riskBasis: value as StrategyDefinition["riskBasis"] })}
        options={[
          { value: "net_debit", label: "Net debit" },
          { value: "net_credit", label: "Net credit" },
          { value: "defined_max_loss", label: "Defined maximum loss" }
        ]}
      />
      <Toggle
        label="Emergency exit enabled"
        checked={strategy.emergencyExitEnabled}
        onChange={emergencyExitEnabled => onChange({ ...strategy, emergencyExitEnabled })}
      />
      <Segmented
        label="Trigger"
        value={strategy.riskMode}
        invalid={invalidFields.has("riskMode")}
        onChange={mode => onChange({
          ...strategy,
          riskMode: mode as StrategyDefinition["riskMode"],
          squareOff: mode !== "legwise" ? "complete" : strategy.squareOff,
          combinedStopLossPercent: mode === "combined_premium" ? stop : strategy.combinedStopLossPercent
        })}
        options={[
          { value: "combined_premium", label: "Combined premium" },
          { value: "strategy_level", label: "Strategy level" },
          { value: "legwise", label: "Per leg" }
        ]}
      />

      {/* Keyed on the trigger, so switching modes cross-fades the replacement
          layout in rather than swapping it between frames. The container tweens
          its height around that where the browser can interpolate `auto`. */}
      <div className="risk-body t-reveal" key={strategy.riskMode}>
      {strategyLevel ? (
        <div className="risk-grid">
          <label className={invalidFields.has("stopLossPercent") || invalidFields.has("combinedStopLossPercent") ? "risk-input invalid" : "risk-input"}>
            <span>Maximum configured loss</span>
            <span className="risk-input-value">
              <input
                type="number"
                min="1"
                max="100"
                value={stop}
                aria-label="Combined stop loss percent"
                onChange={event => {
                  const stopLossPercent = Math.min(100, Math.max(1, Number(event.target.value) || 1));
                  onChange({
                    ...strategy,
                    stopLossPercent,
                    combinedStopLossPercent: strategy.riskMode === "combined_premium" ? stopLossPercent : strategy.combinedStopLossPercent
                  });
                }}
              />
              <b aria-hidden="true">%</b>
            </span>
            <small>Measured against the complete strategy&apos;s entry credit or debit.</small>
          </label>

          <label className={invalidFields.has("takeProfitPercent") ? "risk-input invalid" : "risk-input"}>
            <span>Take profit</span>
            <span className="risk-input-value">
              <input
                type="number"
                min="1"
                max="1000"
                value={strategy.takeProfitPercent}
                aria-label="Take profit percent"
                onChange={event => onChange({ ...strategy, takeProfitPercent: Math.max(1, Number(event.target.value) || 1) })}
              />
              <b aria-hidden="true">%</b>
            </span>
            <small>Closes the complete strategy when this return is reached.</small>
          </label>

          <div className="risk-readout">
            <span>Entry {credit ? "credit" : "debit"}</span>
            <strong>1.0&times;</strong>
            <small>Reference</small>
          </div>
          {/* The figure that decides when both legs close, so it re-enters when
              the stop percentage is edited rather than changing silently. */}
          <div className="risk-readout emphasis">
            <span>Exit trigger</span>
            <strong><AnimatedNumber value={`${exitMultiple.toFixed(1)}×`} /></strong>
            <small>{credit ? "Close cost" : "Liquidation value"}</small>
          </div>

          <label className="risk-input">
            <span>Emergency stop per leg</span>
            <span className="risk-input-value">
              <input
                type="number"
                min="1"
                max="5000"
                value={strategy.emergencyStopLossPercent ?? ""}
                placeholder="Off"
                aria-label="Emergency stop loss percent per leg"
                onChange={event => onChange({
                  ...strategy,
                  emergencyStopLossPercent: event.target.value ? Number(event.target.value) : undefined
                })}
              />
              <b aria-hidden="true">%</b>
            </span>
            <small>Optional hard limit on any single leg.</small>
          </label>
        </div>
      ) : (
        <div className="grid-3">
          <Segmented
            label="Square off"
            value={strategy.squareOff}
            onChange={value => onChange({ ...strategy, squareOff: value as "partial" | "complete" })}
            options={[{ value: "partial", label: "Partial" }, { value: "complete", label: "Complete" }]}
          />
          <Toggle
            label="Trail to break-even"
            checked={strategy.trailToBreakEven}
            onChange={value => onChange({ ...strategy, trailToBreakEven: value })}
          />
          <p className="risk-note">
            <Shield aria-hidden="true" />
            Stops stay inside each leg. Short legs require an explicit stop loss.
          </p>
        </div>
      )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Leg row
 * ------------------------------------------------------------------ */

const STRIKE_MODES = [
  { value: "atm", label: "At the money" },
  { value: "itm", label: "In the money" },
  { value: "otm", label: "Out of the money" },
  { value: "exact", label: "Exact strike" }
];

function LegRow({ leg, index, total, riskMode, open, invalidFields, onToggle, onUpdate, onRemove, onDuplicate, onMove }: {
  leg: StrategyLeg;
  index: number;
  total: number;
  riskMode: StrategyDefinition["riskMode"];
  open: boolean;
  invalidFields: Set<string>;
  onToggle: () => void;
  onUpdate: (patch: Partial<StrategyLeg>) => void;
  onRemove: () => void;
  onDuplicate: () => void;
  onMove: (direction: -1 | 1) => void;
}) {
  const invalid = (field: string) => invalidFields.has(`leg.${leg.id}.${field}`);
  const hasIssue = Array.from(invalidFields).some(field => field.startsWith(`leg.${leg.id}.`));
  const strikeLabel = leg.strikeMode === "exact"
    ? leg.exactStrike ? `Strike ${leg.exactStrike}` : "Strike not set"
    : leg.strikeMode === "atm" ? "ATM" : `${leg.strikeMode.toUpperCase()} ${leg.strikeSteps}`;

  return (
    <li className={hasIssue ? "leg t-acc invalid" : "leg t-acc"} data-open={open}>
      <div className="leg-summary">
        <button type="button" className="leg-toggle" onClick={onToggle} aria-expanded={open}>
          <span className="leg-index">{String(index + 1).padStart(2, "0")}</span>
          <span className={`side-tag ${leg.position}`}>{leg.position === "sell" ? "Short" : "Long"}</span>
          <strong>{leg.optionType === "call" ? "Call" : "Put"}</strong>
          <span className="leg-fact">{leg.lots} {leg.lots === 1 ? "lot" : "lots"}</span>
          <span className="leg-fact">{strikeLabel}</span>
          <span className="leg-fact">{formatExpiry(leg.expiry)}</span>
          <span className="leg-fact">{leg.orderType === "limit_order" ? `Limit ${leg.limitPrice ?? ""}` : "Market"}</span>
          {/* Flipped vertically rather than rotated: it passes through the same
              flat line at the midpoint and animates in every browser. */}
          <span className="leg-chevron t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
        </button>
        <div className="leg-tools">
          <Tooltip label="Move up">
            <button type="button" onClick={() => onMove(-1)} disabled={index === 0} aria-label={`Move leg ${index + 1} up`}><ArrowUp /></button>
          </Tooltip>
          <Tooltip label="Move down">
            <button type="button" onClick={() => onMove(1)} disabled={index === total - 1} aria-label={`Move leg ${index + 1} down`}><ArrowDown /></button>
          </Tooltip>
          <Tooltip label="Duplicate leg">
            <button type="button" onClick={onDuplicate} disabled={total >= MAX_LEGS} aria-label={`Duplicate leg ${index + 1}`}><Copy /></button>
          </Tooltip>
          <Tooltip label="Delete leg">
            <button type="button" className="danger" onClick={onRemove} disabled={total === 1} aria-label={`Delete leg ${index + 1}`}><Trash2 /></button>
          </Tooltip>
        </div>
      </div>

      {/* Height animates through `grid-template-rows: 0fr -> 1fr`, so a leg with
          a limit price field open animates as cleanly as one without and nothing
          has to be measured. The padding stays on the inner element: on a 0fr
          track it would leave a residual strip and the row would never close. */}
      <div className="t-acc-panel" aria-hidden={!open}>
        <div className="t-acc-panel-inner" inert={open ? undefined : true}>
          <div className="leg-body">
            <div className="leg-grid">
              <NumberField label="Lots" min={1} value={leg.lots} invalid={invalid("lots")} onChange={lots => onUpdate({ lots })} />
              <Segmented
                label="Position"
                value={leg.position}
                onChange={value => onUpdate({ position: value as "buy" | "sell" })}
                options={[{ value: "buy", label: "Long" }, { value: "sell", label: "Short" }]}
              />
              <Segmented
                label="Option type"
                value={leg.optionType}
                onChange={value => onUpdate({ optionType: value as "call" | "put" })}
                options={[{ value: "call", label: "Call" }, { value: "put", label: "Put" }]}
              />
              <Field label="Expiry" invalid={invalid("expiry")}>
                <input
                  type="date"
                  min={new Date().toISOString().slice(0, 10)}
                  value={leg.expiry}
                  onChange={event => onUpdate({ expiry: event.target.value })}
                />
              </Field>
              <Select
                label="Strike selection"
                value={leg.strikeMode}
                onChange={value => onUpdate({ strikeMode: value as StrategyLeg["strikeMode"] })}
                options={STRIKE_MODES}
              />
              <Field label="Leg role" hint="Used by validation and automation explanations.">
                <input
                  value={leg.role ?? ""}
                  maxLength={40}
                  placeholder="short_call"
                  onChange={event => onUpdate({ role: event.target.value || undefined })}
                />
              </Field>
              {leg.strikeMode === "exact"
                ? <OptionalNumberField label="Exact strike" value={leg.exactStrike} invalid={invalid("exactStrike")} onChange={exactStrike => onUpdate({ exactStrike })} placeholder="Required" />
                : <NumberField label="Strike steps" min={0} max={100} value={leg.strikeSteps} onChange={strikeSteps => onUpdate({ strikeSteps })} hint={leg.strikeMode === "atm" ? "Ignored at the money." : undefined} />}
            </div>

            <p className="leg-subheading">Order and protection</p>

            <div className="leg-grid">
              <Segmented
                label="Order type"
                value={leg.orderType}
                onChange={value => onUpdate({ orderType: value as "market_order" | "limit_order" })}
                options={[{ value: "market_order", label: "Market" }, { value: "limit_order", label: "Limit" }]}
              />
              {leg.orderType === "limit_order" && (
                <Field label="Limit price" invalid={invalid("limitPrice")}>
                  <input
                    inputMode="decimal"
                    value={leg.limitPrice ?? ""}
                    placeholder="0.00"
                    onChange={event => onUpdate({ limitPrice: event.target.value || undefined })}
                  />
                </Field>
              )}
              {riskMode === "legwise" ? (
                <>
                  <OptionalNumberField label="Target profit" value={leg.targetProfit} onChange={targetProfit => onUpdate({ targetProfit })} />
                  <OptionalNumberField
                    label={leg.position === "sell" ? "Stop loss (required)" : "Stop loss"}
                    value={leg.stopLoss}
                    invalid={invalid("stopLoss")}
                    onChange={stopLoss => onUpdate({ stopLoss })}
                  />
                  <OptionalNumberField label="Trailing stop" value={leg.trailStop} onChange={trailStop => onUpdate({ trailStop })} />
                  <NumberField label="Re-entries after target" min={0} max={10} value={leg.reentryOnTarget} onChange={reentryOnTarget => onUpdate({ reentryOnTarget })} />
                  <NumberField label="Re-entries after stop" min={0} max={10} value={leg.reentryOnStop} onChange={reentryOnStop => onUpdate({ reentryOnStop })} />
                </>
              ) : (
                <p className="leg-note">
                  <Shield aria-hidden="true" />
                  Protected by the strategy-level stop and take profit. Per-leg stops are not used in this mode.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </li>
  );
}
