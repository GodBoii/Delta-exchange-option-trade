import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from news_agent.events import cluster_events, release_comparison, same_story
from news_agent.intelligence import (
    Brief,
    Claim,
    Event,
    Review,
    Verdict,
    analyze_evidence,
    evidence_documents,
    grounded_claim,
    render_events,
    validated_events,
)
from news_agent.sources import Source, _sources, parse_feed

QUOTE = "The bank announced a 25 basis point reduction effective next month."
DOCS = {"d1": {"text": QUOTE, "url": "https://official.example/release", "kind": "article"}}


def event(**kwargs):
    return Event(
        title="Central bank announces monetary policy reduction",
        topic="policy",
        assets=["MACRO"],
        status="announced",
        claims=[Claim(text=QUOTE, quote=QUOTE, source_id="d1")],
        **kwargs,
    )


def test_exact_passage_and_number_checks_reject_fabricated_claims():
    assert grounded_claim(event().claims[0], DOCS)
    assert not grounded_claim(Claim(text="The bank cut 50 basis points.", quote=QUOTE, source_id="d1"), DOCS)
    assert not grounded_claim(
        Claim(text=QUOTE, quote="Invented passage not found in the source.", source_id="d1"), DOCS
    )
    assert not grounded_claim(Claim(text=QUOTE, quote=QUOTE, source_id="missing"), DOCS)


def test_rejected_duplicate_or_missing_review_verdict_is_fail_closed():
    brief = Brief(events=[event(interpretation="Demand could improve.")])
    assert not validated_events(brief, Review(), DOCS)
    verdict = Verdict(claim_id="0:0", supported=True, reason="entailed")
    assert not validated_events(brief, Review(verdicts=[verdict, verdict]), DOCS)
    records = validated_events(brief, Review(verdicts=[verdict]), DOCS)
    assert records[0]["claims"][0]["text"] == QUOTE
    assert records[0]["interpretation"] == ""
    assert records[0]["status"] == "unknown"
    assert "invented" not in render_events(records, [])


def test_event_metadata_requires_its_own_semantic_approval():
    review = Review(
        verdicts=[Verdict(claim_id="0:0", supported=True, reason="source entails statement")],
        event_verdicts=[Verdict(claim_id="0", supported=True, reason="metadata supported")],
    )
    record = validated_events(Brief(events=[event(interpretation="Demand could improve.")]), review, DOCS)[0]
    assert record["metadata_verified"] and record["status"] == "announced"
    assert record["interpretation"]
    partial = event(interpretation="Demand could improve.")
    partial.claims.append(Claim(text="Unsupported second statement", quote=QUOTE, source_id="missing"))
    record = validated_events(Brief(events=[partial]), review, DOCS)[0]
    assert not record["metadata_verified"] and not record["interpretation"]
    with pytest.raises(ValidationError):
        event(event_at=datetime(2026, 10, 23))
    october = validated_events(Brief(events=[event(event_at=datetime(2026, 10, 23, tzinfo=UTC))]), review, DOCS)[0]
    november = validated_events(Brief(events=[event(event_at=datetime(2026, 11, 23, tzinfo=UTC))]), review, DOCS)[0]
    assert october["event_id"] != november["event_id"]


def test_source_inventory_excludes_search_snippets_and_limits_originals():
    tools = [
        {
            "tool_name": "read_news_article",
            "result": json.dumps(
                {
                    "ok": True,
                    "article": {"final_url": "https://official.example/release", "text": QUOTE, "title": "Release"},
                }
            ),
        },
        {"tool_name": "search_news", "result": [{"url": "https://rumor.example/", "body": "Rumor"}]},
    ]
    documents = evidence_documents(tools)
    assert len(documents) == 1 and next(iter(documents.values()))["text"] == QUOTE


@pytest.mark.asyncio
async def test_extraction_and_semantic_review_use_separate_tool_free_calls(monkeypatch):
    calls = []

    class FakeAgent:
        def __init__(self, **kwargs):
            assert kwargs["tools"] == []
            self.schema = kwargs["output_schema"]

        async def arun(self, prompt):
            calls.append(json.loads(prompt))
            result = (
                Brief(events=[event()])
                if self.schema is Brief
                else Review(
                    verdicts=[Verdict(claim_id="0:0", supported=True, reason="supported")],
                    event_verdicts=[Verdict(claim_id="0", supported=True, reason="supported")],
                )
            )
            from agno.run.base import RunStatus

            return SimpleNamespace(status=RunStatus.completed, content=result)

    monkeypatch.setattr("agno.agent.Agent", FakeAgent)
    monkeypatch.setattr("news_agent.agent._create_model", lambda *args: object())
    result = await analyze_evidence(object(), "Untrusted draft with $900 billion fabrication", DOCS, datetime.now(UTC))
    assert len(calls) == 2 and "claims" in calls[1]
    assert result["accepted_claims"] == 1 and "$900" not in result["markdown"]


@pytest.mark.asyncio
async def test_real_agno_structured_output_dispatch_without_provider_requests(monkeypatch):
    from dataclasses import replace

    from agno.models.openrouter import OpenRouter
    from agno.models.response import ModelResponse

    from news_agent.config import NewsAgentSettings

    calls = []
    brief = Brief(events=[event()])
    review = Review(
        verdicts=[Verdict(claim_id="0:0", supported=True, reason="entailed")],
        event_verdicts=[Verdict(claim_id="0", supported=True, reason="supported")],
    )

    async def invoke(self, *args, **kwargs):
        calls.append(kwargs)
        return ModelResponse(role="assistant", content=(brief if len(calls) == 1 else review).model_dump_json())

    monkeypatch.setattr(OpenRouter, "ainvoke", invoke)
    settings = replace(NewsAgentSettings.load(), openrouter_api_key="test-only-not-real")
    result = await analyze_evidence(settings, "Draft", DOCS, datetime.now(UTC))
    assert len(calls) == 2
    assert result["accepted_claims"] == 1 and result["events"][0]["metadata_verified"]


@pytest.mark.asyncio
async def test_prestructured_research_needs_only_one_review_call(monkeypatch):
    from dataclasses import replace

    from agno.models.openrouter import OpenRouter
    from agno.models.response import ModelResponse

    from news_agent.config import NewsAgentSettings

    calls = []
    review = Review(
        verdicts=[Verdict(claim_id="0:0", supported=True, reason="entailed")],
        event_verdicts=[Verdict(claim_id="0", supported=True, reason="supported")],
    )

    async def invoke(self, *args, **kwargs):
        calls.append(kwargs)
        return ModelResponse(role="assistant", content=review.model_dump_json())

    monkeypatch.setattr(OpenRouter, "ainvoke", invoke)
    result = await analyze_evidence(
        replace(NewsAgentSettings.load(), openrouter_api_key="test-only-not-real"),
        "",
        DOCS,
        datetime.now(UTC),
        brief=Brief(events=[event()]),
    )
    assert len(calls) == 1 and result["accepted_claims"] == 1


def test_structured_agent_registers_brief_schema_and_keeps_research_tools():
    from dataclasses import replace

    from agno.db.in_memory import InMemoryDb

    from news_agent.agent import create_news_agent
    from news_agent.config import NewsAgentSettings

    agent = create_news_agent(
        settings=replace(NewsAgentSettings.load(), structured_news=True), db=InMemoryDb(), require_api_key=False
    )
    assert agent.output_schema is Brief
    assert len(agent.tools) == 3


def test_complete_structured_pipeline_dispatches_original_read_then_review(monkeypatch):
    import hashlib
    from dataclasses import replace

    from agno.db.in_memory import InMemoryDb
    from agno.models.openrouter import OpenRouter
    from agno.models.response import ModelResponse

    from news_agent.config import NewsAgentSettings
    from news_agent.pipeline import run_news_pipeline
    from news_agent.tools import FetchResult, parse_article_html

    url = "https://official.example/release"
    body = f"<article><p>{QUOTE * 5}</p></article>".encode()
    text = parse_article_html(body.decode(), url, 128000)["text"][:8000]
    identifier = hashlib.sha256((url + text).encode()).hexdigest()[:20]
    candidate = event()
    candidate.claims[0].source_id = identifier
    requests = []

    async def fetch(*args, **kwargs):
        return FetchResult(url, url, "text/html", body)

    async def invoke(self, *args, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return ModelResponse(
                role="assistant",
                tool_calls=[
                    {
                        "id": "read-original",
                        "type": "function",
                        "function": {"name": "read_news_article", "arguments": json.dumps({"url": url})},
                    }
                ],
            )
        if len(requests) == 2:
            return ModelResponse(role="assistant", content=Brief(events=[candidate]).model_dump_json())
        return ModelResponse(
            role="assistant",
            content=Review(
                verdicts=[Verdict(claim_id="0:0", supported=True, reason="source entails claim")],
                event_verdicts=[Verdict(claim_id="0", supported=True, reason="supported")],
            ).model_dump_json(),
        )

    monkeypatch.setattr(OpenRouter, "ainvoke", invoke)
    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    result = run_news_pipeline(
        "BTC",
        settings=replace(
            NewsAgentSettings.load(), structured_news=True, database_url=None, openrouter_api_key="test-only-not-real"
        ),
        db=InMemoryDb(),
        debug_mode=False,
    )
    assert len(requests) == 3 and result.quality["intelligence"]["accepted_claims"] == 1
    assert QUOTE in result.markdown and url in result.markdown


def test_rdf_sources_parse_dates_and_atom_updates_are_not_publication_dates():
    feed = b"""<rdf:RDF xmlns="http://purl.org/rss/1.0/"
       xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
       xmlns:dc="http://purl.org/dc/elements/1.1/">
       <item><title>Monetary policy release</title><link>https://example.com/release</link>
       <dc:date>2026-10-23T10:00:00Z</dc:date></item></rdf:RDF>"""
    source = Source("BIS", "official", "https://example.com/feed")
    assert parse_feed(feed, source, "now")[0]["published_at"] == "2026-10-23T10:00:00+00:00"
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Policy</title>
        <link href="https://example.com/policy"/><updated>2026-10-23T10:00:00Z</updated></entry></feed>"""
    card = parse_feed(atom, source, "now")[0]
    assert card["published_at"] is None and card["source_updated_at"] is not None


def test_unreliable_connectors_have_explicit_discovery_replacements(monkeypatch):
    monkeypatch.delenv("NEWS_TRY_UNRELIABLE_SOURCES", raising=False)
    replacements = [source for source in _sources("BTC", []) if source.replacement_for]
    assert len(replacements) == 4
    assert all(source.kind == "discovery" and "news.google.com" in source.url for source in replacements)
    monkeypatch.setenv("NEWS_TRY_UNRELIABLE_SOURCES", "true")
    assert not any(source.replacement_for for source in _sources("BTC", []))


def test_clustering_does_not_claim_independence_or_merge_different_release_dates():
    record = {
        "title": "Central bank announces monetary policy reduction",
        "event_at": "2026-10-23T10:00:00Z",
        "status": "announced",
        "source_urls": ["https://example.com/release"],
        "claims": [],
        "event_id": "old",
    }
    groups = cluster_events([record, {**record, "event_id": "new"}], [])
    assert len(groups) == 1 and groups[0]["independent_corroboration"] == "not_established"
    assert not same_story(record, {**record, "event_at": "2026-11-23T10:00:00Z"})
    groups = cluster_events([{**record, "status": "corrected", "event_id": "new"}], [record])
    assert groups[0]["event_id"] == "old" and groups[0]["change"] == "status_update"


def test_calendar_comparison_requires_unit_and_reference_period():
    assert release_comparison({"actual": "3.2", "forecast": "3.1"})["status"] == "insufficient_context"
    result = release_comparison({"actual": "3.2", "forecast": "3.1", "unit": "percent", "reference_period": "2026-09"})
    assert result["actual_minus_forecast"] == "0.1" and result["delta_unit"] == "percentage_points"
    assert (
        release_comparison({"actual": "NaN", "forecast": "3", "unit": "percent", "reference_period": "2026-09"})[
            "status"
        ]
        == "insufficient_context"
    )


def test_initial_collection_accepts_only_recent_snapshot(monkeypatch, tmp_path):
    from news_agent.collector import initial_collection, write_snapshot

    monkeypatch.setenv("NEWS_STATE_DIR", str(tmp_path))
    now = datetime.now(UTC)
    path = tmp_path / "collected" / "BTC.json"
    write_snapshot(path, {"collected_at": now.isoformat(), "result": {"sources": [{"title": "A lead"}]}})
    assert "A lead" in initial_collection("BTC", now)
    assert not initial_collection("BTC", now + timedelta(minutes=4))


def test_structured_pipeline_persists_only_the_reviewed_report(monkeypatch):
    from dataclasses import replace

    from agno.db.in_memory import InMemoryDb
    from agno.run.agent import RunOutput
    from agno.run.base import RunStatus

    from news_agent.config import NewsAgentSettings
    from news_agent.pipeline import run_news_pipeline

    async def research(*args, **kwargs):
        return RunOutput(
            status=RunStatus.completed,
            content="## Summary\n\nUnsupported draft statement.",
            tools=[
                {
                    "tool_name": "read_news_article",
                    "result": json.dumps({"ok": True, "article": {"final_url": DOCS["d1"]["url"], "text": QUOTE}}),
                }
            ],
        )

    async def verify(*args):
        return {"events": [], "gaps": ["Unavailable context"], "accepted_claims": 0}

    monkeypatch.setattr(
        "news_agent.pipeline.create_news_agent",
        lambda **kwargs: SimpleNamespace(arun=research, model=SimpleNamespace(id="test-model")),
    )
    monkeypatch.setattr("news_agent.pipeline.analyze_evidence", verify)
    settings = replace(NewsAgentSettings.load(), database_url=None, structured_news=True)
    evidence = []
    result = run_news_pipeline("BTC", settings=settings, db=InMemoryDb(), evidence_sink=evidence.append)
    assert "Unsupported draft statement" not in result.markdown
    assert "No material claim passed" in result.markdown
    assert evidence[0]["content"] == result.markdown
    assert evidence[0]["reportQuality"]["intelligence"]["accepted_claims"] == 0


def test_review_failure_does_not_release_unverified_draft(monkeypatch):
    from dataclasses import replace

    from agno.db.in_memory import InMemoryDb
    from agno.run.agent import RunOutput
    from agno.run.base import RunStatus

    from news_agent.config import NewsAgentSettings
    from news_agent.pipeline import run_news_pipeline

    async def research(*args, **kwargs):
        return RunOutput(
            status=RunStatus.completed,
            content="## Summary\n\nUnreviewed.",
            tools=[
                {
                    "tool_name": "read_news_article",
                    "result": json.dumps({"ok": True, "article": {"final_url": DOCS["d1"]["url"], "text": QUOTE}}),
                }
            ],
        )

    async def verify(*args):
        raise ValueError("Invalid verification output")

    monkeypatch.setattr(
        "news_agent.pipeline.create_news_agent",
        lambda **kwargs: SimpleNamespace(arun=research, model=SimpleNamespace(id="test-model")),
    )
    monkeypatch.setattr("news_agent.pipeline.analyze_evidence", verify)
    evidence = []
    with pytest.raises(ValueError, match="Invalid verification"):
        run_news_pipeline(
            "BTC",
            settings=replace(NewsAgentSettings.load(), database_url=None, structured_news=True),
            db=InMemoryDb(),
            evidence_sink=evidence.append,
        )
    assert evidence[0]["status"] == "error" and evidence[0]["content"] is None
