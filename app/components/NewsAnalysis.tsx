"use client";

import { useEffect, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { AlertTriangle, History, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type ApiRequester = <T>(url: string, init?: RequestInit) => Promise<T>;

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

function readable(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

function dateTime(value?: string | number | null) {
  if (!value) return "Time not provided";
  const normalized = typeof value === "number" && value < 1_000_000_000_000 ? value * 1_000 : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function runDuration(milliseconds?: number) {
  if (!milliseconds) return "Stored run";
  const seconds = Math.round(milliseconds / 1000);
  return seconds < 60 ? `${seconds}s run` : `${Math.floor(seconds / 60)}m ${seconds % 60}s run`;
}

function MarkdownAnalysis({ children }: { children: string }) {
  return <div className="news-markdown">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ children: linkText, ...props }) => <a {...props} target="_blank" rel="noreferrer">{linkText}</a>,
      }}
    >
      {children}
    </ReactMarkdown>
  </div>;
}

export default function NewsAnalysis({ request }: { request: ApiRequester }) {
  const root = useRef<HTMLDivElement>(null);
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
        signal: null,
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
        preview: response.analysis.replace(/[#*_`>]/g, "").replace(/\s+/g, " ").trim().slice(0, 220),
      }, ...current.filter(session => session.sessionId !== response.sessionId)]);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "The news analysis could not be completed.");
    } finally {
      setRunning(false);
    }
  }

  useGSAP(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.from(".news-markdown-output", { y: 24, opacity: 0, duration: 0.7, ease: "power3.out" });
  }, { scope: root, dependencies: [result?.runId], revertOnUpdate: true });

  return <div className="news-page outcome-page" ref={root}>
    <header className="outcome-page-intro">
      <div>
        <span className="eyebrow"><span />Agent outputs</span>
        <h1>News intelligence outcomes</h1>
        <p>Run the BTC news agent, read its native Markdown analysis, and revisit earlier saved outcomes.</p>
      </div>
      <div className="outcome-page-actions">
        {result && <div className="outcome-saved-status"><i /><span>Saved in Supabase</span><time>{dateTime(result.createdAt)}</time></div>}
        <button className="outcome-run-button" type="button" onClick={() => void runAnalysis()} disabled={running || loadingSaved}>
          <Sparkles />{running ? "Running analysis…" : "Run analysis"}
        </button>
      </div>
    </header>

    <SessionChips
      sessions={sessions}
      selectedSessionId={selectedSessionId}
      disabled={running || loadingSaved}
      onSelect={sessionId => void selectSession(sessionId)}
    />

    <main aria-live="polite">
      {error && <div className="outcome-error" role="alert"><AlertTriangle /><span>{error}</span></div>}
      {running ? <RunningOutcome /> : loadingSaved ? <OutcomeSkeleton /> : result ? <CurrentOutcome result={result} /> : <EmptyOutcome onRun={() => void runAnalysis()} />}
    </main>

    {result && <PastOutcomes outcomes={result.history} />}
  </div>;
}

function SessionChips({ sessions, selectedSessionId, disabled, onSelect }: {
  sessions: NewsSessionSummary[];
  selectedSessionId: string | null;
  disabled: boolean;
  onSelect: (sessionId: string) => void;
}) {
  if (!sessions.length) return null;
  return <section className="news-session-browser" aria-label="Saved news analysis sessions">
    <header><span><History />Past sessions</span><small>{sessions.length} saved</small></header>
    <div className="news-session-chips">
      {sessions.map((session, index) => <button
        type="button"
        key={session.sessionId}
        className={session.sessionId === selectedSessionId ? "active" : ""}
        aria-pressed={session.sessionId === selectedSessionId}
        disabled={disabled}
        onClick={() => onSelect(session.sessionId)}
        title={session.preview}
      >
        <span>{index === 0 ? "Latest" : `Session ${sessions.length - index}`}</span>
        <strong>{dateTime(session.updatedAt || session.createdAt)}</strong>
        <small>{session.runCount} {session.runCount === 1 ? "run" : "runs"}</small>
      </button>)}
    </div>
  </section>;
}

function CurrentOutcome({ result }: { result: NewsAnalysisResponse }) {
  return <section className="news-markdown-output">
    <header className="news-markdown-meta">
      <div><small>Model</small><strong>{result.model}</strong></div>
      <div><small>Research</small><strong>{result.researchTools.map(readable).join(" · ") || "Pre-collected evidence"}</strong></div>
      <div><small>Runtime</small><strong>{runDuration(result.elapsedMs)}</strong></div>
    </header>
    <MarkdownAnalysis>{result.analysis}</MarkdownAnalysis>
    <footer><AlertTriangle />News analysis is probabilistic research, not a trade instruction or execution signal.</footer>
  </section>;
}

function PastOutcomes({ outcomes }: { outcomes: SavedOutcome[] }) {
  return <section className="outcome-history">
    <header><div><span><History />Past outcomes</span><h2>Previous agent outcomes.</h2></div><p>{outcomes.length ? `${outcomes.length} saved outcome${outcomes.length === 1 ? "" : "s"}` : "No earlier outcome is stored."}</p></header>
    {outcomes.length > 0 ? <div className="news-markdown-history">
      {outcomes.map((outcome, index) => <details key={outcome.runId || index}>
        <summary><span>{String(index + 1).padStart(2, "0")}</span><div><time>{dateTime(outcome.createdAt)}</time><strong>{outcome.model}</strong></div></summary>
        <MarkdownAnalysis>{outcome.analysis}</MarkdownAnalysis>
      </details>)}
    </div> : <div className="outcome-history-empty"><History /><span>No previous outcome is stored yet.</span></div>}
  </section>;
}

function OutcomeSkeleton() {
  return <section className="outcome-readonly-state outcome-skeleton" aria-label="Loading saved agent output"><span /><div><i /><i /><i /></div></section>;
}

function RunningOutcome() {
  return <section className="outcome-running-stage" aria-label="News agent is running"><div className="outcome-running-copy">
    <Sparkles /><small>Agno research session</small><h2>Collecting and corroborating market evidence.</h2>
    <p>The agent is searching current sources, assessing BTC transmission channels, and writing its native Markdown analysis.</p>
    <span><i />Analysis in progress</span>
  </div></section>;
}

function EmptyOutcome({ onRun }: { onRun: () => void }) {
  return <section className="outcome-readonly-state"><History /><div><small>No saved output</small><h2>Run the first news analysis.</h2>
    <p>The news agent will research current BTC-relevant events, save the Agno session in Supabase, and show its Markdown response here.</p>
    <button className="outcome-run-button" type="button" onClick={onRun}><Sparkles />Run analysis</button>
  </div></section>;
}
