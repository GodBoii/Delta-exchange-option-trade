"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import Image from "next/image";
import {
  Activity, Bot, CalendarClock, ChevronDown, Play, RefreshCw, ShieldCheck, Workflow
} from "@/app/components/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cleanAgentMarkdown } from "@/lib/agent-markdown";
import {
  AGENT_ASSETS, type AgentAsset, type AutomationOverview as AutomationOverviewData,
  type AutomationRun, type StrategyProposal
} from "@/lib/app-types";
import { backendUrl, requestJson } from "@/lib/api";
import {
  assetOf, countByAsset, filterByAsset, groupScheduleSlots, type AssetFilter, type ScheduleSlot
} from "@/lib/automation-view";
import { useRealtimeSignals } from "@/app/components/RealtimeSignals";
import {
  errorMessage, formatDateTime, formatDayMonth, formatHourMinute, percent, relativeTime, titleCase
} from "@/lib/format";
import { AnimatedNumber, Shimmer, SwapText, Tooltip, useSlidingPill } from "@/app/components/motion";
import {
  EmptyState, InlineMessage, Meter, Panel, PanelHeader, RowMenu, SectionHeading, StatusChip, TableSkeleton,
  TileSkeleton, type NoticeHandler, type RowMenuItem, type StatusTone
} from "@/app/components/ui";

/** "loading" is the first fetch with nothing on screen; "refreshing" keeps the last data visible. */
type LoadPhase = "loading" | "refreshing" | "idle";

const SLOT_PREVIEW = 3;
const HISTORY_PAGE = 8;
const PROPOSAL_PAGE = 6;

export default function Automation({ onNotice, isOwner = false }: { onNotice: NoticeHandler; isOwner?: boolean }) {
  const { automation: revision } = useRealtimeSignals();
  const [overview, setOverview] = useState<AutomationOverviewData | null>(null);
  const [phase, setPhase] = useState<LoadPhase>("loading");
  // Each asset agent runs independently, so one agent's request never blocks the other.
  const [runningAssets, setRunningAssets] = useState<readonly AgentAsset[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setPhase(current => (current === "loading" ? "loading" : "refreshing"));
    try {
      const data = await requestJson<AutomationOverviewData>("/api/automation/overview");
      // Chart links are signed paths on the trading backend, which may be a different origin.
      const runs = await Promise.all(data.runs.map(async run => ({
        ...run,
        charts: await Promise.all(run.charts.map(async chart => ({ ...chart, url: await backendUrl(chart.url) }))),
      })));
      setOverview({ ...data, runs });
      setError("");
    } catch (loadError) {
      setError(errorMessage(loadError, "Automation status could not be loaded."));
    } finally {
      if (!quiet) setPhase("idle");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (revision) void load(true); }, [load, revision]);

  async function updateEnabled(enabled: boolean) {
    if (!overview) return;
    setSaving(true);
    try {
      await requestJson("/api/automation/settings", {
        method: "PUT",
        body: JSON.stringify({ enabled })
      });
      setOverview(current => current && { ...current, settings: { ...current.settings, enabled } });
      onNotice({ tone: "ok", text: enabled ? "Live automation enabled." : "Automation paused and pending agent runs cancelled." });
    } catch (saveError) {
      onNotice({ tone: "error", text: errorMessage(saveError) });
    } finally {
      setSaving(false);
    }
  }

  async function runAnalysis(assets: readonly AgentAsset[]) {
    const queued = assets.filter(asset => !runningAssets.includes(asset));
    if (!queued.length) return;
    setRunningAssets(current => [...current, ...queued]);
    setError("");
    const results = await Promise.allSettled(queued.map(asset => requestJson("/api/automation/run", {
      method: "POST",
      body: JSON.stringify({ reason: `Manual ${asset} review from the Automation workspace`, asset }),
      signal: null
    })));
    const failed = queued.filter((_, index) => results[index].status === "rejected");
    const succeeded = queued.filter(asset => !failed.includes(asset));
    const firstFailure = results.find(result => result.status === "rejected");
    if (firstFailure?.status === "rejected") {
      setError(errorMessage(firstFailure.reason, `The ${failed.join(" and ")} analysis failed. No strategy was activated.`));
    }
    if (succeeded.length) {
      onNotice({ tone: "ok", text: `${succeeded.join(" and ")} analysis queued. The result will appear for everyone.` });
    }
    await load(true);
    setRunningAssets(current => current.filter(asset => !queued.includes(asset)));
  }

  const slots = useMemo(() => groupScheduleSlots(overview?.upcomingRuns ?? []), [overview?.upcomingRuns]);
  const latestDecision = overview?.runs.find(run => run.outcome || run.report);
  const enabled = overview?.settings.enabled ?? false;
  const firstLoad = phase === "loading" && !overview;

  return (
    <div className="automation-page">
      <SectionHeading
        title="Automation"
        description={!overview
          ? undefined
          : enabled
            ? "Your Delta account trades the shared agent decisions with your capital settings."
            : "Paused. Your account skips the shared agent decisions."}
        actions={
          <div className="automation-controls">
            <AutomationSwitch
              enabled={enabled}
              busy={saving}
              disabled={!overview || saving}
              onChange={next => void updateEnabled(next)}
            />
            <Tooltip label="Refresh" placement="bottom">
              <button
                type="button"
                className="button secondary small icon-only"
                aria-label="Refresh automation status"
                onClick={() => void load()}
                disabled={phase !== "idle"}
              >
                <RefreshCw className={phase !== "idle" ? "spin" : ""} aria-hidden="true" />
              </button>
            </Tooltip>
            {isOwner && (
              <RunAnalysisMenu
                runningAssets={runningAssets}
                disabled={!overview?.enabledStrategies}
                onRun={assets => void runAnalysis(assets)}
              />
            )}
          </div>
        }
      />

      {error && <InlineMessage tone="error">{error}</InlineMessage>}

      {firstLoad ? <TileSkeleton count={4} /> : overview && <AutomationStats overview={overview} nextSlot={slots[0]} />}

      <div className="automation-main">
        <LatestDecision run={latestDecision} running={runningAssets.length > 0} loading={firstLoad} />
        <Schedule slots={slots} loading={firstLoad} />
      </div>

      <AgentRuns runs={overview?.runs ?? []} loading={firstLoad} />
      <ProposalList proposals={overview?.proposals ?? []} loading={firstLoad} />
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Controls
 * ------------------------------------------------------------------ */

/**
 * The account's own opt-in to shared decisions. Compact on purpose: it sits in
 * the heading row beside the run command instead of filling a panel.
 */
function AutomationSwitch({ enabled, busy, disabled, onChange }: {
  enabled: boolean;
  busy: boolean;
  disabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const [interacted, setInteracted] = useState(false);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-busy={busy || undefined}
      aria-label="Trade shared agent decisions on my account"
      data-on={enabled}
      disabled={disabled}
      className={`automation-switch t-toggle${enabled ? " on" : ""}${interacted ? " is-init" : ""}`}
      onClick={() => { setInteracted(true); onChange(!enabled); }}
    >
      <i aria-hidden="true"><span className="t-toggle-thumb" /></i>
      <span className="automation-switch-text" aria-hidden="true">
        <span>Automation</span>
        <SwapText>{enabled ? "Live" : "Paused"}</SwapText>
      </span>
    </button>
  );
}

/** Owner-only manual run. One command with the asset choice behind it. */
function RunAnalysisMenu({ runningAssets, disabled, onRun }: {
  runningAssets: readonly AgentAsset[];
  disabled: boolean;
  onRun: (assets: readonly AgentAsset[]) => void;
}) {
  const busy = runningAssets.length > 0;
  const allBusy = AGENT_ASSETS.every(asset => runningAssets.includes(asset));
  const items: RowMenuItem[] = [
    ...AGENT_ASSETS.map(asset => ({
      id: asset,
      label: `Run ${asset} analysis`,
      hint: runningAssets.includes(asset) ? "Already running" : `Manual review by the ${asset} agent`,
      icon: <Play />,
      disabled: runningAssets.includes(asset),
      onSelect: () => onRun([asset])
    })),
    {
      id: "both",
      label: "Run both",
      hint: allBusy ? "Both agents are running" : "BTC and ETH agents in parallel",
      icon: <Workflow />,
      disabled: allBusy,
      onSelect: () => onRun(AGENT_ASSETS)
    }
  ];

  return (
    <RowMenu
      label="Run analysis"
      items={items}
      trigger={{
        className: "button primary small automation-run-trigger",
        disabled: disabled || allBusy,
        content: (
          <>
            <Play aria-hidden="true" />
            {busy ? <Shimmer>{`Analyzing ${runningAssets.join(" + ")}`}</Shimmer> : <span>Run analysis</span>}
            <ChevronDown className="automation-run-caret" aria-hidden="true" />
          </>
        )
      }}
    />
  );
}

/* ------------------------------------------------------------------ *
 * Summary
 * ------------------------------------------------------------------ */

function AutomationStats({ overview, nextSlot }: { overview: AutomationOverviewData; nextSlot?: ScheduleSlot }) {
  const now = useNow(60_000);
  return (
    <dl className="automation-stats">
      <div>
        <dt>Next run</dt>
        <dd>{nextSlot ? relativeTime(nextSlot.scheduledFor, now) : "None scheduled"}</dd>
      </div>
      <div>
        <dt>Strategies</dt>
        <dd>
          <AnimatedNumber value={String(overview.enabledStrategies)} />
          <small>/ {overview.totalStrategies}</small>
        </dd>
      </div>
      <div>
        <dt>Max allocations</dt>
        <dd>
          {overview.settings.maximumConcurrentStrategies != null
            ? <AnimatedNumber value={String(overview.settings.maximumConcurrentStrategies)} />
            : <small>By balance</small>}
        </dd>
      </div>
      <div className="automation-stat-model">
        <dt>Model</dt>
        <dd title={overview.settings.model}>{overview.settings.model}</dd>
      </div>
    </dl>
  );
}

function Schedule({ slots, loading }: { slots: ScheduleSlot[]; loading: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const now = useNow(60_000);
  const preview = slots.slice(0, SLOT_PREVIEW);
  const rest = slots.slice(SLOT_PREVIEW);

  return (
    <Panel className="automation-schedule">
      <PanelHeader icon={<CalendarClock />} title="Upcoming runs" meta={slots.length ? `${slots.length} sessions · IST` : "IST"} />
      {loading ? (
        <TableSkeleton label="upcoming runs" rows={3} />
      ) : slots.length ? (
        <div className="t-acc" data-open={expanded}>
          <ol className="automation-slots">
            {preview.map((slot, index) => <SlotRow key={slot.key} slot={slot} next={index === 0} now={now} />)}
          </ol>
          {rest.length > 0 && (
            <>
              <div className="t-acc-panel" id={listId} aria-hidden={!expanded}>
                <div className="t-acc-panel-inner" inert={expanded ? undefined : true}>
                  <ol className="automation-slots" start={SLOT_PREVIEW + 1}>
                    {rest.map(slot => <SlotRow key={slot.key} slot={slot} next={false} now={now} />)}
                  </ol>
                </div>
              </div>
              <button
                type="button"
                className="automation-more"
                aria-expanded={expanded}
                aria-controls={listId}
                onClick={() => setExpanded(value => !value)}
              >
                <SwapText>{expanded ? "Show fewer" : `Show ${rest.length} more`}</SwapText>
                <span className="t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
              </button>
            </>
          )}
        </div>
      ) : (
        <EmptyState compact icon={<CalendarClock />} title="No upcoming runs" description="The next session appears after the schedule syncs." />
      )}
    </Panel>
  );
}

function SlotRow({ slot, next, now }: { slot: ScheduleSlot; next: boolean; now: number }) {
  return (
    <li className={next ? "automation-slot is-next" : "automation-slot"}>
      <time dateTime={slot.scheduledFor} className="automation-slot-time">
        <strong>{formatHourMinute(slot.scheduledFor)}</strong>
        <small>{formatDayMonth(slot.scheduledFor)}</small>
      </time>
      <span className="automation-slot-name">
        <strong>{titleCase(slot.trigger)}</strong>
        {next && <small>Next · {relativeTime(slot.scheduledFor, now)}</small>}
      </span>
      <span className="automation-slot-assets">
        {slot.assets.map(asset => <AssetMark key={asset} asset={asset} />)}
      </span>
    </li>
  );
}

function LatestDecision({ run, running, loading }: { run?: AutomationRun; running: boolean; loading: boolean }) {
  return (
    <Panel className="automation-decision">
      <PanelHeader
        icon={<Bot />}
        title="Latest decision"
        meta={run ? formatDateTime(run.completedAt ?? run.startedAt ?? run.scheduledFor) : "No completed decision"}
        actions={run ? (
          <span className="automation-decision-tags">
            <AssetMark asset={assetOf(run)} />
            <StatusChip tone={runTone(run)}>{titleCase(run.outcome ?? run.status)}</StatusChip>
          </span>
        ) : undefined}
      />
      {loading ? (
        <TableSkeleton label="latest decision" rows={4} />
      ) : run?.report ? (
        <div className="automation-run-output">
          <RunCharts charts={run.charts} />
          <ClampedReport markdown={run.report} />
        </div>
      ) : run?.error ? (
        <InlineMessage tone="error">{run.error}</InlineMessage>
      ) : (
        <EmptyState
          compact
          icon={<Bot />}
          title={running ? "Analysis is running" : "No automation decision yet"}
          description={running
            ? "Market analysis or a scheduled strategy recheck is running."
            : "A completed agent outcome appears here."}
        />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 * Lists
 * ------------------------------------------------------------------ */

function AgentRuns({ runs, loading }: { runs: AutomationRun[]; loading: boolean }) {
  const [filter, setFilter] = useState<AssetFilter>("all");
  const [limit, setLimit] = useState(HISTORY_PAGE);
  const visible = filterByAsset(runs, filter);

  return (
    <Panel className="automation-runs">
      <PanelHeader icon={<Activity />} title="Agent runs" meta={`${runs.length} runs`} />
      <AssetFilterBar
        label="Filter agent runs by asset"
        value={filter}
        counts={countByAsset(runs)}
        onChange={next => { setFilter(next); setLimit(HISTORY_PAGE); }}
      />
      {loading ? (
        <TableSkeleton label="agent runs" rows={5} />
      ) : visible.length ? (
        <>
          <ul className="agent-run-list">
            {visible.slice(0, limit).map(run => <AgentRunItem key={run.id} run={run} />)}
          </ul>
          {visible.length > limit && (
            <button type="button" className="automation-more" onClick={() => setLimit(value => value + HISTORY_PAGE)}>
              {moreLabel(visible.length - limit, HISTORY_PAGE)}
            </button>
          )}
        </>
      ) : (
        <EmptyState
          compact
          icon={<Activity />}
          title={runs.length ? `No ${filter} runs` : "No agent runs"}
          description={runs.length ? "Pick another asset to see its runs." : "Runs appear here after Automation completes its first analysis."}
        />
      )}
    </Panel>
  );
}

function AgentRunItem({ run }: { run: AutomationRun }) {
  const [open, setOpen] = useState(false);
  // The report and charts are heavy, so they mount on first open and then stay
  // mounted, which lets the collapse animate instead of the content vanishing.
  const [mounted, setMounted] = useState(false);
  const bodyId = useId();

  return (
    <li className="agent-run t-acc" data-open={open}>
      <button
        type="button"
        className="agent-run-summary"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => { setMounted(true); setOpen(value => !value); }}
      >
        <AssetMark asset={assetOf(run)} />
        <span className="agent-run-title">
          <strong>{titleCase(run.trigger)}</strong>
          <small>{formatDateTime(run.scheduledFor)}{run.scope === "historical_account" ? " · Earlier account run" : ""}</small>
        </span>
        <StatusChip tone={runTone(run)}>{titleCase(run.outcome ?? run.status)}</StatusChip>
        <span className="t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
      </button>
      <div className="t-acc-panel" id={bodyId} aria-hidden={!open}>
        <div className="t-acc-panel-inner" inert={open ? undefined : true}>
          {mounted && (
            <div className="agent-run-body">
              <RunCharts charts={run.charts} />
              {run.report ? (
                <article className="agent-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanAgentMarkdown(run.report)}</ReactMarkdown>
                </article>
              ) : run.error ? (
                <InlineMessage tone="error">{run.error}</InlineMessage>
              ) : (
                <p className="agent-run-pending">This run has not produced a report yet.</p>
              )}
              {(run.sessionId || run.runId) && (
                <dl className="automation-run-ids">
                  {run.sessionId && <div><dt>Session</dt><dd>{run.sessionId}</dd></div>}
                  {run.runId && <div><dt>Run</dt><dd>{run.runId}</dd></div>}
                </dl>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

function ProposalList({ proposals, loading }: { proposals: StrategyProposal[]; loading: boolean }) {
  const [filter, setFilter] = useState<AssetFilter>("all");
  const [limit, setLimit] = useState(PROPOSAL_PAGE);
  const visible = filterByAsset(proposals, filter);

  return (
    <Panel className="automation-proposals">
      <PanelHeader icon={<ShieldCheck />} title="Strategy proposals" meta={`${proposals.length} saved`} />
      <AssetFilterBar
        label="Filter proposals by asset"
        value={filter}
        counts={countByAsset(proposals)}
        onChange={next => { setFilter(next); setLimit(PROPOSAL_PAGE); }}
      />
      {loading ? (
        <TableSkeleton label="strategy proposals" rows={3} />
      ) : visible.length ? (
        <>
          <ul className="proposal-grid">
            {visible.slice(0, limit).map(proposal => <ProposalCard key={proposal.id} proposal={proposal} />)}
          </ul>
          {visible.length > limit && (
            <button type="button" className="automation-more" onClick={() => setLimit(value => value + PROPOSAL_PAGE)}>
              {moreLabel(visible.length - limit, PROPOSAL_PAGE)}
            </button>
          )}
        </>
      ) : (
        <EmptyState
          compact
          icon={<ShieldCheck />}
          title={proposals.length ? `No ${filter} proposals` : "No proposals"}
          description={proposals.length
            ? "Pick another asset to see its proposals."
            : "A proposal appears only after every saved-strategy and account gate passes."}
        />
      )}
    </Panel>
  );
}

function ProposalCard({ proposal }: { proposal: StrategyProposal }) {
  const [expanded, setExpanded] = useState(false);
  const reason = useRef<HTMLParagraphElement>(null);
  const overflowing = useOverflow(reason, proposal.reasoning);
  const reasonId = useId();
  const confidence = Math.round(proposal.confidence * 100);

  return (
    <li className="proposal-card">
      <header className="proposal-card-head">
        <AssetMark asset={assetOf(proposal)} />
        <strong className="proposal-card-name">{proposal.strategyName}</strong>
        <StatusChip tone={proposal.status === "rejected" ? "negative" : "active"}>{titleCase(proposal.status)}</StatusChip>
      </header>
      <dl className="proposal-card-facts">
        <div><dt>Activation</dt><dd>{formatDateTime(proposal.activationTime)}</dd></div>
        <div>
          <dt>Confidence</dt>
          <dd className="proposal-confidence">
            <Meter value={confidence} max={100} label={`Confidence ${confidence}%`} tone="active" />
            <span>{percent(confidence, 0)}</span>
          </dd>
        </div>
      </dl>
      <p
        ref={reason}
        id={reasonId}
        className={expanded ? "proposal-card-reason is-expanded" : "proposal-card-reason"}
      >
        {proposal.reasoning}
      </p>
      {(overflowing || expanded) && (
        <button
          type="button"
          className="automation-inline-toggle"
          aria-expanded={expanded}
          aria-controls={reasonId}
          onClick={() => setExpanded(value => !value)}
        >
          <SwapText>{expanded ? "Show less" : "Read full reason"}</SwapText>
        </button>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ *
 * Shared pieces
 * ------------------------------------------------------------------ */

function AssetFilterBar({ label, value, counts, onChange }: {
  label: string;
  value: AssetFilter;
  counts: Record<AssetFilter, number>;
  onChange: (value: AssetFilter) => void;
}) {
  const { barRef, pill } = useSlidingPill(value, '[aria-pressed="true"]');
  const options: { id: AssetFilter; label: string }[] = [
    { id: "all", label: "All" },
    ...AGENT_ASSETS.map(asset => ({ id: asset, label: asset }))
  ];
  return (
    <div className="filter-bar automation-filter" role="group" aria-label={label} ref={barRef}>
      {pill}
      {options.map(option => (
        <button
          type="button"
          key={option.id}
          aria-pressed={value === option.id}
          className="filter-tab"
          onClick={() => onChange(option.id)}
        >
          {option.label}<span><AnimatedNumber value={String(counts[option.id])} /></span>
        </button>
      ))}
    </div>
  );
}

function AssetMark({ asset }: { asset: AgentAsset }) {
  return <span className="asset-mark" data-asset={asset}>{asset}</span>;
}

/** Long agent reports start clamped so the decision panel does not push the page down. */
function ClampedReport({ markdown }: { markdown: string }) {
  const [expanded, setExpanded] = useState(false);
  const body = useRef<HTMLElement>(null);
  const overflowing = useOverflow(body, markdown);
  const bodyId = useId();

  return (
    <div className="automation-report-wrap" data-expanded={expanded} data-overflowing={overflowing}>
      <article ref={body} id={bodyId} className="automation-report agent-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanAgentMarkdown(markdown)}</ReactMarkdown>
      </article>
      {(overflowing || expanded) && (
        <button
          type="button"
          className="automation-inline-toggle"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded(value => !value)}
        >
          <SwapText>{expanded ? "Show less" : "Read full report"}</SwapText>
        </button>
      )}
    </div>
  );
}

function RunCharts({ charts }: { charts: AutomationRun["charts"] }) {
  if (!charts.length) return null;
  return (
    <section className="automation-chart-section" aria-label="Charts supplied to this agent run">
      <header>
        <strong>Agent chart inputs</strong>
        <small>{charts.length} signed images from this run</small>
      </header>
      <div className="automation-chart-grid">
        {charts.map(chart => (
          <figure key={chart.id}>
            <Image src={chart.url} alt={chart.altText} width={1200} height={640} unoptimized />
            <figcaption>{chart.label}</figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

/** Colour follows what the run decided, not only whether it finished: a finished "no trade" is neutral. */
function runTone(run: Pick<AutomationRun, "status" | "outcome">): StatusTone {
  if (run.status === "failed") return "negative";
  if (run.status !== "completed") return "active";
  return run.outcome === "activated" ? "positive" : "neutral";
}

function moreLabel(remaining: number, page: number) {
  return remaining <= page ? `Show ${remaining} more` : `Show ${page} more · ${remaining} left`;
}

/** Current time, re-read on an interval so relative labels ("in 4h") stay true. */
function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/**
 * Whether a clamped element hides content. Measured while clamped, so the
 * expand control only appears when there is something to reveal.
 */
function useOverflow(ref: RefObject<HTMLElement | null>, content: ReactNode) {
  const [overflowing, setOverflowing] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => {
      // An expanded element no longer clips, so keep the last clamped answer.
      if (node.classList.contains("is-expanded") || node.closest('[data-expanded="true"]')) return;
      setOverflowing(node.scrollHeight - node.clientHeight > 2);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref, content]);
  return overflowing;
}
