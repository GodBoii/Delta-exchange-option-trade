"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Bot, CalendarClock, Play, RefreshCw, ShieldCheck, Workflow } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AutomationOverview as AutomationOverviewData } from "@/lib/app-types";
import { requestJson } from "@/lib/api";
import { errorMessage, formatDateTime, percent, titleCase } from "@/lib/format";
import {
  EmptyState, InlineMessage, Panel, PanelHeader, SectionHeading, Shimmer, StatusChip, Toggle,
  type NoticeHandler
} from "@/app/components/ui";

export default function Automation({ onNotice }: { onNotice: NoticeHandler }) {
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

  const latest = overview?.runs[0];

  return (
    <div className="automation-page">
      <SectionHeading
        eyebrow="Agent operations"
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
            <div><dt>Maximum active slots</dt><dd>3</dd></div>
            <div><dt>Execution</dt><dd>Existing live strategy scheduler</dd></div>
          </dl>
        </Panel>

        <Panel>
          <PanelHeader icon={<CalendarClock />} title="Automatic run times" meta="Timezone-aware market sessions" />
          <ul className="automation-sessions">
            <li><span>Asia</span><strong>09:00 Tokyo</strong><small>05:30 IST</small></li>
            <li><span>London</span><strong>08:00 London</strong><small>12:30 or 13:30 IST</small></li>
            <li><span>New York</span><strong>09:30 New York</strong><small>19:00 or 20:00 IST</small></li>
          </ul>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          icon={<Workflow />}
          title="Latest decision"
          meta={latest ? `${titleCase(latest.status)} · ${formatDateTime(latest.scheduledFor)}` : "No run saved"}
        />
        {latest?.report ? (
          <article className="automation-report news-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{latest.report}</ReactMarkdown>
          </article>
        ) : latest?.error ? (
          <InlineMessage tone="error">{latest.error}</InlineMessage>
        ) : (
          <EmptyState
            compact
            icon={<Bot />}
            title={running ? "Analysis is running" : "No automation decision yet"}
            description={running
              ? "The main agent is reading five BTC charts and delegating current-news research to its news agent."
              : "Run the first analysis after saving at least one strategy for automation."}
          />
        )}
      </Panel>

      <Panel>
        <PanelHeader icon={<Activity />} title="Main agent run history" meta={`${overview?.runs.length ?? 0} saved runs`} />
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
                  {run.report ? (
                    <article className="news-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{run.report}</ReactMarkdown></article>
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
