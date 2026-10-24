import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agno.db.in_memory import InMemoryDb
from agno.models.response import ToolExecution

from news_agent.config import NewsAgentSettings
from news_agent.failures import NewsResearchError
from news_agent.intelligence import Brief, Claim, Event, Review, Verdict, render_events
from news_agent.pipeline import run_news_pipeline
from news_agent.research import original_candidates
from news_agent.social import topic_matches
from news_agent.sources import PublicSourceTools, Source
from news_agent.tools import FetchResult, NewsResearchTools

QUOTE = "The central bank announced a 25 basis point rate reduction effective next month."
URL = "https://official.example/release"


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.asyncio
async def test_actual_curator_dispatches_replacement_rss_and_direct_json(monkeypatch, direct):
    from news_agent import sources
    from news_agent.budget import ResearchBudget
    from news_agent.source_cache import SourceCache

    monkeypatch.setattr(sources, "SOURCE_CACHE", SourceCache())
    entries = (
        [Source("GDELT asset", "discovery", "https://example.com/gdelt.json")]
        if direct
        else [
            Source(
                "GDELT asset discovery",
                "discovery",
                "https://example.com/rss",
                replacement_for="https://original.example",
            )
        ]
    )
    monkeypatch.setattr(sources, "_sources", lambda *args: entries)
    body = (
        json.dumps(
            {"articles": [{"title": "Policy change announced", "url": URL, "seendate": "20261024T100000Z"}]}
        ).encode()
        if direct
        else (
            (
                f"<rss><channel><item><title>Policy change announced</title><link>{URL}</link></item></channel></rss>"
            ).encode()
        )
    )

    async def fetch(url, *args, **kwargs):
        return FetchResult(url, url, "application/json" if direct else "application/rss+xml", body)

    monkeypatch.setattr(sources, "fetch_public_document", fetch)
    result = json.loads(await PublicSourceTools(object(), ResearchBudget()).curate_public_sources("BTC", []))
    assert len(result["sources"]) == 1 and not result["errors"]


@pytest.mark.parametrize(
    ("topic", "text", "expected"),
    [
        ("Ethereum ETH", "Whether traders agree", False),
        ("ETH", "Sethi discusses markets", False),
        ("Ethereum ETH staking", "BTC staking proposal", False),
        ("ETH", "$ETH demand", True),
        ("Ethereum", "Ethereum network upgrade", True),
        ("Bitcoin BTC", "ABTCDEF unrelated code", False),
        ("Ethereum ETH", "ETHUSDT liquidity", True),
        ("BTC", "#Bitcoin moves", True),
        ("ETH", "ETH/BTC ratio", True),
    ],
)
def test_social_asset_tokens_have_boundaries(topic, text, expected):
    assert topic_matches(topic, text) is expected


def test_specific_gaps_are_preserved_as_plain_text():
    report = render_events([], ["The BLS calendar was unavailable.", "![inject](https://bad.example)"])
    assert "BLS calendar was unavailable" in report
    assert "![inject]" not in report


def test_empty_brief_requires_an_explicit_limitation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Brief.model_validate({"events": [], "gaps": []})
    assert not Brief(gaps=["Sources were read but no material finding was established."]).events


def test_undated_recurring_stories_do_not_merge_on_url_alone():
    from news_agent.events import same_story

    left = {
        "title": "Monthly consumer price release",
        "source_urls": [URL],
        "claims": [{"quote": "September values", "published_at": "2026-10-01T10:00:00Z"}],
    }
    right = {**left, "claims": [{"quote": "October values", "published_at": "2026-11-01T10:00:00Z"}]}
    assert not same_story(left, right)
    assert same_story(left, left)


def test_original_selection_balances_sources_and_excludes_discovery():
    cards = [{"kind": "official", "url": f"https://official.example/{i}"} for i in range(8)]
    cards += [
        {"kind": "publisher", "url": "https://publisher.example/a"},
        {"kind": "organization", "url": "https://exchange.example/a"},
        {"kind": "discovery", "url": "https://news.google.com/lead"},
    ]
    urls = original_candidates(cards)
    assert len(urls) == 4 and "https://publisher.example/a" in urls
    assert not any("google" in url for url in urls)


def test_preparation_prioritizes_asset_leads_and_diversifies_publishers():
    cards = [
        {"kind": "publisher", "title": "XRP development", "url": "https://broad.example/1"},
        {"kind": "publisher", "title": "Ethereum upgrade", "url": "https://ether.example/1"},
        {"kind": "publisher", "title": "ETH validators", "url": "https://ether.example/2"},
        {"kind": "publisher", "title": "Bitcoin adoption", "url": "https://bitcoin.example/1"},
        {
            "kind": "publisher",
            "title": "ETH historical release",
            "url": "https://old.example/1",
            "older_than_window": True,
        },
    ]
    eth = original_candidates(cards, asset="ETH")
    btc = original_candidates(cards, asset="BTC")
    assert eth[0] == "https://ether.example/1" and btc[0] == "https://bitcoin.example/1"
    assert eth.index("https://broad.example/1") < eth.index("https://ether.example/2")
    assert eth[-1] == "https://old.example/1"


@pytest.mark.parametrize("has_evidence", [False, True])
def test_empty_provider_response_cannot_fake_research_at_http_boundary(monkeypatch, has_evidence):
    from news_agent.agent import _create_model as production_model

    requests, persisted = [], []
    brief = Brief(
        events=[
            Event(
                title="Central bank announces a rate reduction",
                topic="rates",
                status="announced",
                assets=["MACRO"],
                claims=[Claim(text=QUOTE, quote=QUOTE, source_id="d1")],
            )
        ]
    )
    review = Review(
        verdicts=[Verdict(claim_id="0:0", supported=True, reason="entailed")],
        event_verdicts=[Verdict(claim_id="0", supported=True, reason="supported")],
    )

    async def prepare(toolkits, asset, executions):
        executions.append(
            ToolExecution(
                tool_call_id="prepared:test",
                tool_name="read_news_article",
                tool_args={"url": URL},
                result=json.dumps(
                    {"ok": has_evidence, "article": {"final_url": URL, "text": QUOTE, "evidence_id": "d1"}}
                    if has_evidence
                    else {"ok": False, "url": URL, "error": "blocked"}
                ),
            )
        )
        return "Evidence prepared" if has_evidence else "No original source available"

    def respond(request):
        body = json.loads(request.content)
        assert body.get("response_format", {}).get("type") != "json_object"
        requests.append(body)
        if len(requests) == 1:
            assert body.get("max_tokens") == 12000
            assert body.get("response_format", {}).get("type") != "json_object"
            assert len(body["tools"]) == 7
            content = '{"events":[],"gaps":[]}'
        else:
            assert not body.get("tools")
            assert body.get("max_tokens") == 6000
            content = (brief if len(requests) == 2 else review).model_dump_json()
        return httpx.Response(
            200,
            json={
                "id": "test",
                "model": "test",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    def model(settings, require_api_key):
        result = production_model(settings, require_api_key)
        result.http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        return result

    monkeypatch.setattr("news_agent.agent._create_model", model)
    monkeypatch.setattr("news_agent.pipeline.prepare_research", prepare)
    settings = replace(
        NewsAgentSettings.load(), structured_news=True, database_url=None, openrouter_api_key="test-only"
    )
    if has_evidence:
        result = run_news_pipeline(
            "BTC", settings=settings, db=InMemoryDb(), evidence_sink=persisted.append, debug_mode=False
        )
        assert len(requests) == 3 and result.quality["research_status"] == "verified_findings"
        assert result.research_trace[0]["origin"] == "pipeline" and QUOTE in result.markdown
    else:
        with pytest.raises(NewsResearchError) as caught:
            run_news_pipeline(
                "BTC", settings=settings, db=InMemoryDb(), evidence_sink=persisted.append, debug_mode=False
            )
        assert caught.value.failure["code"] == "news_research_incomplete"
        assert len(requests) == 1 and persisted[0]["status"] == "error"


@pytest.mark.asyncio
async def test_cancelled_article_can_be_retried_within_budget(monkeypatch):
    started = asyncio.Event()
    attempts = []

    async def fetch(url, *args, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            started.set()
            await asyncio.Event().wait()
        return FetchResult(url, url, "text/html", (f"<article><p>{QUOTE * 5}</p></article>").encode())

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    tools = NewsResearchTools(SimpleNamespace(allowed_domains=()))
    task = asyncio.create_task(tools.read_news_article(URL))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert json.loads(await tools.read_news_article(URL))["ok"] and len(attempts) == 2


def test_missing_news_blocks_strategy_before_market_or_strategy_tools(monkeypatch):
    from automation_agent import team

    monkeypatch.setattr(team, "read_parent_run_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(team, "MarketIntelligenceTools", lambda **kwargs: object())
    monkeypatch.setattr(
        team,
        "run_news_pipeline",
        lambda *args, **kwargs: SimpleNamespace(
            quality={"research_status": "researched_no_findings"}, research_trace=[]
        ),
    )
    with pytest.raises(NewsResearchError) as caught:
        team.run_automation_team(
            settings=SimpleNamespace(structured_news=True),
            user_id="global",
            agent_run_id="test",
            session_id="test",
            account_context={},
            trigger="manual",
        )
    assert caught.value.failure["code"] == "news_evidence_insufficient"


@pytest.mark.asyncio
async def test_legacy_account_name_is_optional_but_identity_is_unchanged():
    from app.account_registry import AccountRegistry

    row = {"status": "connected", "delta_user_id": "42"}
    registry = AccountRegistry(
        SimpleNamespace(pool=None, local_data=SimpleNamespace(request=AsyncMock(return_value=row)))
    )
    result = await registry.main("owner")
    assert result["account_name"] == "Main account" and result["delta_user_id"] == "42"
    assert "account_name" not in row


def test_timeout_without_message_keeps_useful_classification():
    from news_agent.failures import provider_failure

    result = provider_failure(TimeoutError())
    assert result["code"] == "model_timeout" and result["message"]


@pytest.mark.parametrize(
    "quality",
    [
        {},
        {"research_status": "verified_findings"},
        {"research_status": "verified_findings", "original_reads": 0, "intelligence": {"accepted_claims": 2}},
        {"research_status": "verified_findings", "original_reads": 2, "intelligence": {"accepted_claims": 0}},
        {"research_status": "limited_findings", "original_reads": 2, "intelligence": {"accepted_claims": 2}},
    ],
)
def test_verified_label_alone_cannot_authorize_strategy(quality):
    from news_agent.pipeline import strategy_evidence_ready

    assert not strategy_evidence_ready(quality)
    assert strategy_evidence_ready(
        {"research_status": "verified_findings", "original_reads": 1, "intelligence": {"accepted_claims": 1}}
    )


def test_direct_social_read_remains_discussion_not_independent_news():
    from news_agent.intelligence import evidence_documents

    documents = evidence_documents(
        [
            {
                "tool_name": "read_news_article",
                "result": json.dumps(
                    {"ok": True, "article": {"final_url": "https://x.com/example/status/123", "text": QUOTE}}
                ),
            }
        ]
    )
    assert next(iter(documents.values()))["kind"] == "discussion"


@pytest.mark.parametrize(
    ("text", "quote", "expected"),
    [
        ("The year was 2026.", "The year was 2026 for this published report.", True),
        ("Inflation was 3.1%.", "The reported inflation figure was 3.1 percent.", True),
        ("The reported value was 1000.", "The reported value was 1,000 in this release.", True),
        ("The adjustment was -25.", "The adjustment was 25 according to the release.", False),
        ("The rate was 25%.", "The rate was 25 basis points in this release.", False),
    ],
)
def test_grounding_normalizes_formatting_without_changing_sign_or_units(text, quote, expected):
    from news_agent.intelligence import grounded_claim

    assert grounded_claim(Claim(text=text, quote=quote, source_id="d"), {"d": {"text": quote}}) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["invalid JSON", '{"events":[],"gaps":[]}', '{"unexpected":true}'])
async def test_invalid_extraction_is_classified_without_releasing_draft(monkeypatch, response):
    from datetime import UTC, datetime

    from news_agent.agent import _create_model as production_model
    from news_agent.intelligence import analyze_evidence

    def respond(request):
        body = json.loads(request.content)
        assert not body.get("tools") and not body.get("response_format")
        return httpx.Response(
            200,
            json={
                "id": "test",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": response}}
                ],
            },
        )

    def model(settings, require_api_key):
        result = production_model(settings, require_api_key)
        result.http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        return result

    monkeypatch.setattr("news_agent.agent._create_model", model)
    settings = replace(NewsAgentSettings.load(), openrouter_api_key="test-only", database_url=None)
    with pytest.raises(NewsResearchError) as caught:
        await analyze_evidence(settings, "Unverified draft", {"d": {"text": QUOTE, "url": URL}}, datetime.now(UTC))
    assert caught.value.failure["code"] == "news_verification_invalid"


def test_verification_failure_preserves_prepared_trace_and_blocks_report(monkeypatch):
    from agno.run.agent import RunOutput
    from agno.run.base import RunStatus

    execution = ToolExecution(
        tool_call_id="prepared:originals",
        tool_name="read_news_article",
        result=json.dumps({"ok": True, "article": {"final_url": URL, "text": QUOTE}}),
    )

    async def prepare(toolkits, asset, executions):
        executions.append(execution)
        return "Original retrieved"

    async def research(*args, **kwargs):
        return RunOutput(status=RunStatus.completed, content="Unverified draft")

    async def verify(*args):
        raise NewsResearchError({"code": "news_verification_invalid", "stage": "news", "message": "Invalid"}, [])

    monkeypatch.setattr("news_agent.pipeline.prepare_research", prepare)
    monkeypatch.setattr("news_agent.pipeline.analyze_evidence", verify)
    monkeypatch.setattr(
        "news_agent.pipeline.create_news_agent",
        lambda **kwargs: SimpleNamespace(arun=research, model=SimpleNamespace(id="test")),
    )
    persisted = []
    with pytest.raises(NewsResearchError) as caught:
        run_news_pipeline(
            "BTC",
            settings=replace(NewsAgentSettings.load(), structured_news=True, database_url=None),
            db=InMemoryDb(),
            evidence_sink=persisted.append,
        )
    assert caught.value.trace[0]["origin"] == "pipeline"
    assert persisted[0]["status"] == "error" and persisted[0]["content"] is None
    assert persisted[0]["researchTrace"] == caught.value.trace
