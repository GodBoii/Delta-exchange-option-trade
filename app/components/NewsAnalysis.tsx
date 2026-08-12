"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import Image from "next/image";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ExternalLink,
  FileSearch,
  Gauge,
  History,
  Newspaper,
  Sparkles,
} from "lucide-react";

type ApiRequester = <T>(url: string, init?: RequestInit) => Promise<T>;
type Direction = "bullish" | "bearish" | "neutral" | "mixed" | "uncertain";
type Volatility = "low" | "moderate" | "high" | "extreme" | "uncertain";

type DirectionalAssessment = {
  asset: string;
  direction: Direction;
  confidence: number;
  mechanisms: string[];
};

type NewsEvent = {
  headline: string;
  summary: string;
  event_type: string;
  event_status: string;
  entities: string[];
  novelty: number;
  btc_relevance: number;
  volatility_impact: Volatility;
  expected_horizon: string;
  directional_assessments: DirectionalAssessment[];
  is_corroborated: boolean;
  source_urls: string[];
  uncertainties: string[];
};

type NewsSource = {
  title: string;
  url: string;
  publisher?: string | null;
  published_at?: string | null;
  source_class: string;
  evidence_used: string;
};

type NewsImage = {
  image_url: string;
  source_page_url: string;
  alt_text?: string | null;
  caption?: string | null;
};

type NewsReport = {
  query: string;
  analyzed_at: string;
  executive_summary: string;
  events: NewsEvent[];
  aggregate_btc_direction: Direction;
  aggregate_volatility_risk: Volatility;
  contradictions: string[];
  missing_information: string[];
  sources: NewsSource[];
  images: NewsImage[];
  risk_notice: string;
};

type SavedOutcome = {
  runId?: string | null;
  model: string;
  report: NewsReport;
};

type NewsAnalysisResponse = SavedOutcome & {
  success: boolean;
  sessionId: string;
  researchTools: string[];
  elapsedMs?: number;
  history: SavedOutcome[];
};

const DEFAULT_SESSION = "btc-news-desk";

function readable(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

function percent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function dateTime(value?: string | null) {
  if (!value) return "Time not provided";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function runDuration(milliseconds?: number) {
  if (!milliseconds) return "Stored run";
  const seconds = Math.round(milliseconds / 1000);
  return seconds < 60 ? `${seconds}s run` : `${Math.floor(seconds / 60)}m ${seconds % 60}s run`;
}

export default function NewsAnalysis({ request }: { request: ApiRequester }) {
  const root = useRef<HTMLDivElement>(null);
  const [result, setResult] = useState<NewsAnalysisResponse | null>(null);
  const [loadingSaved, setLoadingSaved] = useState(true);

  useEffect(() => {
    request<NewsAnalysisResponse>(`/api/news/sessions/${DEFAULT_SESSION}`, {
      signal: AbortSignal.timeout(10_000),
    })
      .then(setResult)
      .catch(() => undefined)
      .finally(() => setLoadingSaved(false));
  }, [request]);

  useGSAP(
    () => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      gsap.registerPlugin(ScrollTrigger);
      gsap.from(".outcome-verdict-word", {
        yPercent: 105,
        opacity: 0,
        stagger: 0.1,
        duration: 1.05,
        ease: "power4.out",
      });
      gsap.fromTo(
        ".outcome-reveal-copy",
        { opacity: 0.22, y: 28 },
        {
          opacity: 1,
          y: 0,
          ease: "none",
          scrollTrigger: { trigger: ".outcome-reveal-copy", start: "top 88%", end: "top 58%", scrub: 0.7 },
        },
      );
      gsap.utils.toArray<HTMLElement>(".outcome-event-card").forEach((card, index) => {
        gsap.fromTo(
          card,
          { scale: 0.965 - Math.min(index, 4) * 0.008, opacity: 0.7 },
          {
            scale: 1,
            opacity: 1,
            ease: "none",
            scrollTrigger: { trigger: card, start: "top 86%", end: "top 34%", scrub: 0.8 },
          },
        );
      });
    },
    { scope: root, dependencies: [result?.runId, result?.history.length], revertOnUpdate: true },
  );

  return (
    <div className="news-page outcome-page" ref={root}>
      <header className="outcome-page-intro">
        <div>
          <span className="eyebrow"><span />Agent outputs</span>
          <h1>News intelligence outcomes</h1>
          <p>A read-only record of the latest BTC market assessment and earlier saved outcomes.</p>
        </div>
        {result && <div className="outcome-saved-status"><i /><span>Saved in Supabase</span><time>{dateTime(result.report.analyzed_at)}</time></div>}
      </header>

      <main aria-live="polite">
        {loadingSaved ? (
          <OutcomeSkeleton />
        ) : result ? (
          <CurrentOutcome result={result} />
        ) : (
          <EmptyOutcome />
        )}
      </main>

      {result && <PastOutcomes outcomes={result.history} />}
    </div>
  );
}

function CurrentOutcome({ result }: { result: NewsAnalysisResponse }) {
  const report = result.report;
  const leadImage = report.images[0];
  return <>
    <section className="outcome-hero">
      <div className="outcome-hero-copy">
        <div className="outcome-status-line"><span><i />Current outcome</span><time>{dateTime(report.analyzed_at)}</time></div>
        <h1 aria-label={`BTC ${readable(report.aggregate_btc_direction)} signal`}>
          <span className="outcome-word-mask"><span className="outcome-verdict-word">BTC</span></span>
          <span className="outcome-word-mask"><span className={`outcome-verdict-word tone-${report.aggregate_btc_direction}`}>{readable(report.aggregate_btc_direction)}</span></span>
          <span className="outcome-word-mask"><span className="outcome-verdict-word">signal</span></span>
        </h1>
        <p className="outcome-summary outcome-reveal-copy">{report.executive_summary}</p>
        <div className="outcome-hero-stats">
          <div><small>Volatility</small><strong className={`vol-${report.aggregate_volatility_risk}`}>{readable(report.aggregate_volatility_risk)}</strong></div>
          <div><small>Evidence</small><strong>{report.sources.length} sources</strong></div>
          <div><small>Runtime</small><strong>{runDuration(result.elapsedMs)}</strong></div>
        </div>
      </div>

      <aside className="outcome-hero-visual">
        {leadImage ? <figure>
          <Image
            src={leadImage.image_url}
            alt={leadImage.alt_text || leadImage.caption || "Image published by a cited news source"}
            width={1000}
            height={1160}
            sizes="(max-width: 820px) 100vw, 42vw"
            unoptimized
            priority
            referrerPolicy="no-referrer"
            onError={event => event.currentTarget.parentElement?.classList.add("broken")}
          />
          <figcaption><span>{leadImage.caption || leadImage.alt_text || "Sourced news image"}</span><a href={leadImage.source_page_url} target="_blank" rel="noreferrer">Source <ArrowUpRight /></a></figcaption>
        </figure> : <div className="outcome-signal-plate"><Sparkles /><span>Structured agent verdict</span><strong>{readable(report.aggregate_volatility_risk)}</strong><small>volatility risk</small></div>}
      </aside>
    </section>

    <section className="outcome-evidence-grid">
      <article className="outcome-evidence-card outcome-evidence-wide">
        <small>Agent trace</small><strong>{result.model}</strong>
        <p>{Array.from(new Set(result.researchTools)).map(readable).join(" · ") || "Saved structured analysis"}</p>
      </article>
      <article className="outcome-evidence-card"><small>Events assessed</small><strong>{report.events.length.toString().padStart(2, "0")}</strong><p>High-impact developments returned by the analyst.</p></article>
      <article className="outcome-evidence-card"><small>Unresolved</small><strong>{(report.contradictions.length + report.missing_information.length).toString().padStart(2, "0")}</strong><p>Contradictions and missing information remain visible.</p></article>
    </section>

    <section className="outcome-events">
      <header><span>What shaped the outcome</span><strong>{report.events.length.toString().padStart(2, "0")}</strong></header>
      <div className="outcome-event-stack">
        {report.events.length ? report.events.map((event, index) => <OutcomeEvent event={event} index={index} key={`${event.headline}-${index}`} />) : (
          <div className="outcome-no-events"><FileSearch /><div><h2>No event cleared the evidence threshold.</h2><p>The latest verdict is driven by incomplete or conflicting evidence.</p></div></div>
        )}
      </div>
    </section>

    <section className="outcome-sources">
      <header><span>Evidence trail</span><strong>{report.sources.length.toString().padStart(2, "0")}</strong></header>
      <div className="outcome-source-list">
        {report.sources.map((source, index) => <a href={source.url} target="_blank" rel="noreferrer" key={`${source.url}-${index}`}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div><strong>{source.title}</strong><small>{source.publisher || readable(source.source_class)} · {dateTime(source.published_at)}</small><p>{source.evidence_used}</p></div>
          <ArrowUpRight />
        </a>)}
      </div>
      <footer><AlertTriangle /><span>{report.risk_notice}</span></footer>
    </section>
  </>;
}

function OutcomeEvent({ event, index }: { event: NewsEvent; index: number }) {
  const btcAssessment = event.directional_assessments.find(item => item.asset.toLowerCase().includes("btc")) || event.directional_assessments[0];
  const stackStyle = { "--stack-index": Math.min(index, 5) } as CSSProperties;
  return <article className="outcome-event-card" style={stackStyle}>
    <header>
      <span>{String(index + 1).padStart(2, "0")}</span>
      <div><small>{readable(event.event_type)} · {readable(event.expected_horizon)}</small><h2>{event.headline}</h2></div>
      <em className={event.is_corroborated ? "verified" : "single"}>{event.is_corroborated ? <CheckCircle2 /> : <AlertTriangle />}{event.is_corroborated ? "Corroborated" : "Single source"}</em>
    </header>
    <p>{event.summary}</p>
    <div className="outcome-event-metrics">
      <span><small><Gauge />BTC relevance</small><strong>{percent(event.btc_relevance)}</strong></span>
      <span><small>Expected direction</small><strong className={btcAssessment ? `tone-${btcAssessment.direction}` : "tone-uncertain"}>{btcAssessment ? readable(btcAssessment.direction) : "Uncertain"}</strong></span>
      <span><small>Volatility impact</small><strong className={`vol-${event.volatility_impact}`}>{readable(event.volatility_impact)}</strong></span>
    </div>
  </article>;
}

function PastOutcomes({ outcomes }: { outcomes: SavedOutcome[] }) {
  return <section className="outcome-history">
    <header><div><span><History />Past outcomes</span><h2>Previous agent outcomes.</h2></div><p>{outcomes.length ? `${outcomes.length} saved outcome${outcomes.length === 1 ? "" : "s"}` : "No earlier outcome is stored."}</p></header>
    {outcomes.length > 0 ? <div className="outcome-history-rail">
      {outcomes.map((outcome, index) => <details className="outcome-history-card" key={outcome.runId || `${outcome.report.analyzed_at}-${index}`}>
        <summary>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div><time>{dateTime(outcome.report.analyzed_at)}</time><h3 className={`tone-${outcome.report.aggregate_btc_direction}`}>{readable(outcome.report.aggregate_btc_direction)}</h3><p>{outcome.report.executive_summary}</p></div>
          <strong className={`vol-${outcome.report.aggregate_volatility_risk}`}>{readable(outcome.report.aggregate_volatility_risk)} volatility</strong>
        </summary>
        <div className="outcome-history-detail">
          <span><Newspaper />{outcome.report.events.length} events</span>
          <span><ExternalLink />{outcome.report.sources.length} sources</span>
          <p>{outcome.report.risk_notice}</p>
        </div>
      </details>)}
    </div> : <div className="outcome-history-empty"><History /><span>No previous outcome is stored yet.</span></div>}
  </section>;
}

function OutcomeSkeleton() {
  return <section className="outcome-readonly-state outcome-skeleton" aria-label="Loading saved agent output">
    <span /><div><i /><i /><i /></div>
  </section>;
}

function EmptyOutcome() {
  return <section className="outcome-readonly-state">
    <History />
    <div><small>No saved output</small><h2>Waiting for an agent outcome.</h2><p>This page only reads saved results. Agent execution will live in a separate workflow.</p></div>
  </section>;
}
