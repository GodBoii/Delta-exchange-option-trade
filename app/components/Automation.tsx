"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import { Activity, Bot, CalendarClock, Play, RefreshCw, ShieldCheck, Workflow } from "@/app/components/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cleanAgentMarkdown } from "@/lib/agent-markdown";
import type { AutomationOverview as AutomationOverviewData } from "@/lib/app-types";
import { requestJson } from "@/lib/api";
import { useRealtimeSignals } from "@/app/components/RealtimeSignals";
import { errorMessage, formatDateTime, percent, titleCase } from "@/lib/format";
import {
  EmptyState, InlineMessage, Panel, PanelHeader, SectionHeading, Shimmer, StatusChip, Toggle,
  type NoticeHandler
} from "@/app/components/ui";

export default function Automation({ onNotice }: { onNotice: NoticeHandler }) {
  const { automation: revision } = useRealtimeSignals();
  const [overview, setOverview] = useState<AutomationOverviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setOverview(await requestJson<AutomationOverviewData>("/api/automation/overview"));
      setError("");
    } catch (loadError) {
      setError(errorMessage(loadError, "Automation status could not be loaded."));
    } finally {
      if (!quiet) setLoading(false);
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
      setOverview({ ...overview, settings: { ...overview.settings, enabled } });
      onNotice({ tone: "ok", text: enabled ? "Live automation enabled." : "Automation paused and pending agent runs cancelled." });
    } catch (saveError) {
      onNotice({ tone: "error", text: errorMessage(saveError) });
    } finally {
      setSaving(false);
    }
  }

  async function runNow() {
    setRunning(true);
    setError("");
    try {
      await requestJson("/api/automation/run", {
        method: "POST",
        body: JSON.stringify({ reason: "Manual review from the Automation workspace" }),
        signal: null
      });
      await load(true);
      onNotice({ tone: "ok", text: "Automation analysis completed and its decision was saved." });
    } catch (runError) {
      setError(errorMessage(runError, "The automation analysis failed. No strategy was activated."));
      await load(true);
    } finally {
      setRunning(false);
    }
  }

  const latestDecision = overview?.runs.find(run => run.outcome || run.report);
  const upcomingRuns = overview?.upcomingRuns ?? [];

  return (
    <div className="automation-page">
      <SectionHeading
        title="Automation"
        description="Analyze BTC charts, news, options, account risk, and schedule saved strategies live."
        actions={
          <>
            <button type="button" className="button secondary" onClick={() => void load()} disabled={loading || running}>
              <RefreshCw className={loading ? "spin" : ""} aria-hidden="true" />Refresh
            </button>
            <button type="button" className="button primary" onClick={() => void runNow()} disabled={loading || running || !overview?.settings.enabled || !overview?.enabledStrategies}>
              <Play aria-hidden="true" />{running ? <Shimmer>Analyzing market</Shimmer> : "Run analysis"}
            </button>
          </>
        }
      />

      {error && <InlineMessage tone="error">{error}</InlineMessage>}

      <div className="automation-summary">
        <Panel>
          <PanelHeader icon={<Bot />} title="Agent status" meta={overview?.settings.model ?? "Loading model"} />
          <Toggle
            label="Automation"
            description="Runs at the listed market sessions and at follow-up times selected by the agent."
            checked={overview?.settings.enabled ?? false}
            onChange={enabled => { if (!loading && !saving) void updateEnabled(enabled); }}
          />
          <dl className="automation-facts">
            <div><dt>Strategies available</dt><dd>{overview ? `${overview.enabledStrategies} of ${overview.totalStrategies}` : "Loading"}</dd></div>
            <div><dt>Maximum active allocations</dt><dd>{overview?.settings.maximumConcurrentStrategies ?? "Based on USD balance"}</dd></div>
            <div><dt>Execution</dt><dd>Existing live strategy scheduler</dd></div>
          </dl>
        </Panel>

        <Panel>
          <PanelHeader icon={<CalendarClock />} title="Upcoming runs" meta="Earliest first · IST" />
          {upcomingRuns.length ? (
            <ol className="automation-sessions">
              {upcomingRuns.map((run, index) => (
                <li key={run.id}>
                  <span>{index ? "Then" : "Next"}</span>
                  <strong>{titleCase(run.trigger)}</strong>
                  <small>{formatDateTime(run.scheduledFor)}</small>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState compact icon={<CalendarClock />} title="No upcoming runs" description="The next session will appear after the schedule syncs." />
          )}
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          icon={<Workflow />}
          title="Latest decision"
          meta={latestDecision
            ? `${titleCase(latestDecision.outcome ?? latestDecision.status)} · ${formatDateTime(latestDecision.completedAt ?? latestDecision.startedAt)}`
            : "No completed decision"}
        />
        {latestDecision?.report ? (
          <div className="automation-run-output">
            <RunCharts charts={latestDecision.charts} />
            <article className="automation-report news-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanAgentMarkdown(latestDecision.report)}</ReactMarkdown>
            </article>
          </div>
        ) : latestDecision?.error ? (
          <InlineMessage tone="error">{latestDecision.error}</InlineMessage>
        ) : (
          <EmptyState
            compact
            icon={<Bot />}
            title={running ? "Analysis is running" : "No automation decision yet"}
            description={running
              ? "The main agent is reading five BTC charts and delegating current-news research to its news agent."
              : "A completed agent outcome will appear here."}
          />
        )}
      </Panel>

      <Panel>
        <PanelHeader icon={<Activity />} title="Main agent run history" meta={`${overview?.runs.length ?? 0} actual runs`} />
        {overview?.runs.length ? (
          <div className="automation-run-list">
            {overview.runs.map(run => (
              <details key={run.id} className="automation-run-item">
                <summary>
                  <span>
                    <strong>{titleCase(run.trigger)}</strong>
                    <small>{formatDateTime(run.scheduledFor)}</small>
                  </span>
                  <StatusChip tone={run.status === "failed" ? "negative" : run.status === "completed" ? "positive" : "active"}>
                    {titleCase(run.outcome ?? run.status)}
                  </StatusChip>
                </summary>
                <div className="automation-run-body">
                  <RunCharts charts={run.charts} />
                  {run.report ? (
                    <article className="news-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanAgentMarkdown(run.report)}</ReactMarkdown></article>
                  ) : run.error ? (
                    <InlineMessage tone="error">{run.error}</InlineMessage>
                  ) : (
                    <p>This run has not produced a report yet.</p>
                  )}
                  {(run.sessionId || run.runId) && (
                    <dl className="automation-run-ids">
                      {run.sessionId && <div><dt>Session</dt><dd>{run.sessionId}</dd></div>}
                      {run.runId && <div><dt>Run</dt><dd>{run.runId}</dd></div>}
                    </dl>
                  )}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <EmptyState compact icon={<Activity />} title="No main-agent runs" description="Runs appear here after Automation completes its first analysis." />
        )}
      </Panel>

      <Panel>
        <PanelHeader icon={<ShieldCheck />} title="Strategy proposals" meta={`${overview?.proposals.length ?? 0} saved proposals`} />
        {overview?.proposals.length ? (
          <div className="table-scroll mobile-card-list">
            <table className="data-table mobile-card-table">
              <caption className="visually-hidden">Saved AI strategy proposals</caption>
              <thead><tr><th>Strategy</th><th>Status</th><th>Activation</th><th>Confidence</th><th>Reason</th></tr></thead>
              <tbody>
                {overview.proposals.map(proposal => (
                  <tr key={proposal.id}>
                    <th scope="row">{proposal.strategyName} <small>v{proposal.strategyVersion}</small></th>
                    <td data-label="Status"><StatusChip tone={proposal.status === "rejected" ? "negative" : "active"}>{titleCase(proposal.status)}</StatusChip></td>
                    <td data-label="Activation">{formatDateTime(proposal.activationTime)}</td>
                    <td data-label="Confidence">{percent(proposal.confidence * 100, 0)}</td>
                    <td data-label="Reason">{proposal.reasoning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState compact icon={<ShieldCheck />} title="No proposals" description="A proposal appears only after every saved-strategy and account gate passes." />
        )}
      </Panel>
    </div>
  );
}

function RunCharts({ charts }: { charts: AutomationOverviewData["runs"][number]["charts"] }) {
  if (!charts.length) return null;
  return (
    <section className="automation-chart-section" aria-label="Charts supplied to this agent run">
      <header>
        <strong>Agent chart inputs</strong>
        <small>{charts.length} signed images from this exact run</small>
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
