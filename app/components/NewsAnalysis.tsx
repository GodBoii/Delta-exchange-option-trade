"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, History, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ThinkingOrb } from "thinking-orbs";
import type { ApiRequester } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import {
  HoverGroup, InlineMessage, SectionHeading, Shimmer, StatusDot, SwapText
} from "@/app/components/ui";

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
        if (active) setError(errorMessage(loadError, "Saved news reports could not be loaded."));
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
      setError(errorMessage(loadError, "The saved news report could not be loaded."));
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
      setError(errorMessage(runError, "The news analysis could not be completed. Please try again."));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="news-page">
      <SectionHeading
        eyebrow="Market research"
        title="Bitcoin news analysis"
        description="Review current Bitcoin news, its possible market impact, and your earlier reports."
        actions={
          <>
            {result && (
              <div className="news-saved-state">
                <span><StatusDot tone="active" />Saved</span>
                <time>{agentDateTime(result.createdAt)}</time>
              </div>
            )}
            <button
              type="button"
              className="button primary"
              onClick={() => void runAnalysis()}
              disabled={running || loadingSaved}
            >
              <Sparkles aria-hidden="true" />
              {running
                ? <Shimmer>Running analysis</Shimmer>
                : <SwapText>Run analysis</SwapText>}
            </button>
          </>
        }
      />

      {sessions.length > 0 && (
        <section className="news-sessions" aria-label="Saved news reports">
          <header>
            <span><History aria-hidden="true" />Analysis history</span>
            <small>{sessions.length} {sessions.length === 1 ? "report" : "reports"}</small>
          </header>
          {/* A horizontal stack of sibling cards, so hovering one lifts it and
              nudges its neighbours with a distance falloff. The return springs,
              which is the one place hover-out is the richer half of the motion. */}
          <HoverGroup className="news-session-chips">
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
                <span>{index === 0 ? "Latest" : `Previous ${sessions.length - index}`}</span>
                <strong>{agentDateTime(session.updatedAt || session.createdAt)}</strong>
                <small>{session.runCount} {session.runCount === 1 ? "analysis" : "analyses"}</small>
              </button>
            ))}
          </HoverGroup>
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
    <article className="news-report t-reveal">
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
        <h2>Earlier analyses</h2>
        <p>{outcomes.length
          ? `${outcomes.length} earlier ${outcomes.length === 1 ? "analysis" : "analyses"} in this report history`
          : "No earlier analysis is available."}</p>
      </header>
      {outcomes.length > 0 && (
        <div className="news-history-list">
          {outcomes.map((outcome, index) => (
            <OutcomeDisclosure key={outcome.runId || index} outcome={outcome} index={index} />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * A stored analysis, collapsed.
 *
 * A real accordion rather than a native `details`, because each of these is
 * several screens of Markdown: opening one should grow the row through
 * `grid-template-rows: 0fr -> 1fr` instead of snapping the page height, and the
 * chevron should flip rather than jump.
 */
function OutcomeDisclosure({ outcome, index }: { outcome: SavedOutcome; index: number }) {
  const [open, setOpen] = useState(false);
  const panelId = `news-outcome-${outcome.runId || index}`;

  return (
    <div className="news-history-item t-acc" data-open={open}>
      <button
        type="button"
        className="news-history-head"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen(value => !value)}
      >
        <span>{String(index + 1).padStart(2, "0")}</span>
        <time>{agentDateTime(outcome.createdAt)}</time>
        <strong>Previous analysis</strong>
        <span className="t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
      </button>
      <div className="t-acc-panel" id={panelId} aria-hidden={!open}>
        <div className="t-acc-panel-inner" inert={open ? undefined : true}>
          <MarkdownAnalysis>{outcome.analysis}</MarkdownAnalysis>
        </div>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <section className="news-status-panel" aria-label="Loading saved news analysis">
      <span className="news-orb">
        <ThinkingOrb state="working" size={64} theme="dark" aria-label="Loading the saved analysis" />
      </span>
      <div className="news-skeleton" aria-hidden="true">
        <i className="skeleton" /><i className="skeleton" /><i className="skeleton" />
      </div>
    </section>
  );
}

/**
 * The agent's activity state.
 *
 * A run takes one to three minutes, so the indicator states which verb the agent
 * is on — searching sources — rather than a spinner that only says "busy". This
 * is a semantic readout of what the system is actually doing, which is the only
 * reason an orb belongs here at all.
 */
function RunningState() {
  return (
    <section className="news-status-panel" aria-label="News analysis is running">
      <span className="news-orb">
        <ThinkingOrb state="searching" size={64} theme="dark" aria-label="Collecting and corroborating market evidence" />
      </span>
      <div>
        <small>News research in progress</small>
        <h2>Checking current market evidence.</h2>
        <p>
          We are reviewing current sources, checking material claims, and assessing possible Bitcoin
          price and volatility effects. This usually takes one to three minutes.
        </p>
        <span className="news-progress">
          <i aria-hidden="true" /><Shimmer>Analysis in progress</Shimmer>
        </span>
      </div>
    </section>
  );
}

function IdleState({ onRun }: { onRun: () => void }) {
  return (
    <section className="news-status-panel t-reveal">
      <History aria-hidden="true" />
      <div>
        <small>No analysis yet</small>
        <h2>Create your first Bitcoin news report.</h2>
        <p>
          The report reviews current Bitcoin-related events, checks important claims, and saves the
          completed analysis to your history.
        </p>
        <button type="button" className="button primary" onClick={onRun}>
          <Sparkles aria-hidden="true" />Run analysis
        </button>
      </div>
    </section>
  );
}
