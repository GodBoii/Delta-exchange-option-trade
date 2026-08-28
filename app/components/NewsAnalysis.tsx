"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, History } from "@/app/components/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ThinkingOrb } from "thinking-orbs";
import type { ApiRequester } from "@/lib/api";
import { cleanAgentMarkdown } from "@/lib/agent-markdown";
import { errorMessage } from "@/lib/format";
import {
  HoverGroup, InlineMessage, SectionHeading, StatusDot
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
        {cleanAgentMarkdown(children)}
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
    if (sessionId === selectedSessionId) return;
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

  return (
    <div className="news-page">
      <SectionHeading
        title="Bitcoin news analysis"
        description="News analyses produced by the news member during main automation runs."
        actions={
          result && (
            <div className="news-saved-state">
              <span><StatusDot tone="active" />Saved from automation</span>
              <time>{agentDateTime(result.createdAt)}</time>
            </div>
          )
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
                disabled={loadingSaved}
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
        {loadingSaved ? <LoadingState /> : result ? <Report result={result} /> : <EmptyNewsState />}
      </div>
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

function LoadingState() {
  return (
    <section className="news-status-panel" aria-label="Loading saved news analysis">
      <span className="news-orb">
        <ThinkingOrb state="working" size={64} theme="auto" aria-label="Loading the saved analysis" />
      </span>
      <div className="news-skeleton" aria-hidden="true">
        <i className="skeleton" /><i className="skeleton" /><i className="skeleton" />
      </div>
    </section>
  );
}

function EmptyNewsState() {
  return (
    <section className="news-status-panel t-reveal">
      <History aria-hidden="true" />
      <div>
        <small>No analysis yet</small>
        <h2>No automation news report has been saved.</h2>
        <p>
          Turn on Automation. The news member&apos;s report from each completed main-agent run will appear here.
        </p>
      </div>
    </section>
  );
}
