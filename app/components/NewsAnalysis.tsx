"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, History, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ApiRequester } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { InlineMessage, SectionHeading, StatusDot } from "@/app/components/ui";

type SavedOutcome = {
  runId?: string | null;
  model: string;
  analysis: string;
  createdAt?: string | number | null;
};

type NewsAnalysisResponse = SavedOutcome & {
  success: boolean;
  sessionId: string;
  researchTools: string[];
  elapsedMs?: number;
  history: SavedOutcome[];
};

type NewsSessionSummary = {
  sessionId: string;
  runId?: string | null;
  model: string;
  createdAt?: string | number | null;
  updatedAt?: string | number | null;
  runCount: number;
  preview: string;
};

type NewsSessionListResponse = {
  success: boolean;
  sessions: NewsSessionSummary[];
};

const DEFAULT_QUERY = "Analyze today's highest-impact Bitcoin news, corroborate the material claims, and explain the likely BTC direction and volatility channels.";

/** Agno timestamps arrive as ISO strings or epoch seconds depending on the store. */
function agentDateTime(value?: string | number | null) {
  if (!value) return "Time not recorded";
  const normalized = typeof value === "number" && value < 1_000_000_000_000 ? value * 1_000 : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function runDuration(milliseconds?: number) {
  if (!milliseconds) return "Stored run";
  const seconds = Math.round(milliseconds / 1000);
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function MarkdownAnalysis({ children }: { children: string }) {
  return (
    <div className="news-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children: linkText, ...props }) => <a {...props} target="_blank" rel="noreferrer">{linkText}</a>
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Reader for the news agent's own Markdown output.
 *
 * The agent writes prose, so this surface does not attempt to reshape it into
 * invented scorecards or sentiment gauges. It presents the run's provenance
 * (model, research tools, runtime), the analysis as written, and the earlier
 * saved outcomes for the same session.
 */
export default function NewsAnalysis({ request }: { request: ApiRequester }) {
  const [result, setResult] = useState<NewsAnalysisResponse | null>(null);
  const [sessions, setSessions] = useState<NewsSessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [loadingSaved, setLoadingSaved] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    request<NewsSessionListResponse>("/api/news/sessions")
      .then(async response => {
        if (!active) return;
        setSessions(response.sessions);
        const first = response.sessions[0];
        if (!first) return;
        setSelectedSessionId(first.sessionId);
        const saved = await request<NewsAnalysisResponse>(`/api/news/sessions/${first.sessionId}`);
        if (active) setResult(saved);
      })
      .catch(loadError => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Saved news sessions could not be loaded.");
      })
      .finally(() => { if (active) setLoadingSaved(false); });
    return () => { active = false; };
  }, [request]);

  async function selectSession(sessionId: string) {
    if (sessionId === selectedSessionId || running) return;
    setSelectedSessionId(sessionId);
    setLoadingSaved(true);
    setError(null);
    try {
      setResult(await request<NewsAnalysisResponse>(`/api/news/sessions/${sessionId}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "The saved news session could not be loaded.");
    } finally {
      setLoadingSaved(false);
    }
  }

  async function runAnalysis() {
    setRunning(true);
    setError(null);
    try {
      const sessionId = `btc-news-${Date.now()}`;
      const response = await request<NewsAnalysisResponse>("/api/news/analyze", {
        method: "POST",
        body: JSON.stringify({ query: DEFAULT_QUERY, sessionId }),
        // Agent research runs far longer than a normal request; no client timeout.
        signal: null
      });
      setResult(response);
      setSelectedSessionId(response.sessionId);
      setSessions(current => [{
        sessionId: response.sessionId,
        runId: response.runId,
        model: response.model,
        createdAt: response.createdAt,
        updatedAt: response.createdAt,
        runCount: response.history.length + 1,
        preview: response.analysis.replace(/[#*_`>]/g, "").replace(/\s+/g, " ").trim().slice(0, 220)
      }, ...current.filter(session => session.sessionId !== response.sessionId)]);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "The news analysis could not be completed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="news-page">
      <SectionHeading
        eyebrow="Agent research"
        title="News intelligence"
        description="Run the BTC news agent, read its analysis as written, and revisit earlier saved outcomes."
        actions={
          <>
            {result && (
              <div className="news-saved-state">
                <span><StatusDot tone="active" />Saved in Supabase</span>
                <time>{agentDateTime(result.createdAt)}</time>
              </div>
            )}
            <button
              type="button"
              className="button primary"
              onClick={() => void runAnalysis()}
              disabled={running || loadingSaved}
            >
              <Sparkles aria-hidden="true" />{running ? "Running analysis" : "Run analysis"}
            </button>
          </>
        }
      />

      {sessions.length > 0 && (
        <section className="news-sessions" aria-label="Saved news analysis sessions">
          <header>
            <span><History aria-hidden="true" />Sessions</span>
            <small>{sessions.length} saved</small>
          </header>
          <div className="news-session-chips">
            {sessions.map((session, index) => (
              <button
                type="button"
                key={session.sessionId}
                className={session.sessionId === selectedSessionId ? "active" : ""}
                aria-pressed={session.sessionId === selectedSessionId}
                disabled={running || loadingSaved}
                onClick={() => void selectSession(session.sessionId)}
                title={session.preview}
              >
                <span>{index === 0 ? "Latest" : `Session ${sessions.length - index}`}</span>
                <strong>{agentDateTime(session.updatedAt || session.createdAt)}</strong>
                <small>{session.runCount} {session.runCount === 1 ? "run" : "runs"}</small>
              </button>
            ))}
          </div>
        </section>
      )}

      <div aria-live="polite">
        {error && <InlineMessage tone="error">{error}</InlineMessage>}
        {running ? <RunningState /> : loadingSaved ? <LoadingState /> : result ? <Report result={result} /> : <IdleState onRun={() => void runAnalysis()} />}
      </div>

      {result && <EarlierOutcomes outcomes={result.history} />}
    </div>
  );
}

function Report({ result }: { result: NewsAnalysisResponse }) {
  return (
    <article className="news-report">
      <header className="news-report-meta">
        <div><small>Model</small><strong>{result.model}</strong></div>
        <div>
          <small>Research tools</small>
          <strong>{result.researchTools.map(titleCase).join(" · ") || "Pre-collected evidence"}</strong>
        </div>
        <div><small>Runtime</small><strong>{runDuration(result.elapsedMs)}</strong></div>
      </header>
      <MarkdownAnalysis>{result.analysis}</MarkdownAnalysis>
      <footer className="news-report-footer">
        <AlertTriangle aria-hidden="true" />
        News analysis is probabilistic research, not a trade instruction or execution signal.
      </footer>
    </article>
  );
}

function EarlierOutcomes({ outcomes }: { outcomes: SavedOutcome[] }) {
  return (
    <section className="news-history">
      <header>
        <h2>Earlier outcomes</h2>
        <p>{outcomes.length
          ? `${outcomes.length} stored ${outcomes.length === 1 ? "outcome" : "outcomes"} in this session`
          : "No earlier outcome is stored in this session."}</p>
      </header>
      {outcomes.length > 0 && (
        <div className="news-history-list">
          {outcomes.map((outcome, index) => (
            <details key={outcome.runId || index}>
              <summary>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <time>{agentDateTime(outcome.createdAt)}</time>
                <strong>{outcome.model}</strong>
              </summary>
              <MarkdownAnalysis>{outcome.analysis}</MarkdownAnalysis>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}

function LoadingState() {
  return (
    <section className="news-status-panel" aria-label="Loading saved agent output">
      <Sparkles aria-hidden="true" />
      <div className="news-skeleton" aria-hidden="true">
        <i className="skeleton" /><i className="skeleton" /><i className="skeleton" />
      </div>
    </section>
  );
}

function RunningState() {
  return (
    <section className="news-status-panel" aria-label="News agent is running">
      <Sparkles aria-hidden="true" />
      <div>
        <small>Agno research session</small>
        <h2>Collecting and corroborating market evidence.</h2>
        <p>
          The agent is searching current sources, assessing BTC transmission channels, and writing
          its analysis. Runs typically take one to three minutes.
        </p>
        <span className="news-progress"><i aria-hidden="true" />Analysis in progress</span>
      </div>
    </section>
  );
}

function IdleState({ onRun }: { onRun: () => void }) {
  return (
    <section className="news-status-panel">
      <History aria-hidden="true" />
      <div>
        <small>No saved output</small>
        <h2>Run the first news analysis.</h2>
        <p>
          The agent researches current BTC-relevant events, saves the Agno session in Supabase, and
          returns its written analysis here.
        </p>
        <button type="button" className="button primary" onClick={onRun}>
          <Sparkles aria-hidden="true" />Run analysis
        </button>
      </div>
    </section>
  );
}
