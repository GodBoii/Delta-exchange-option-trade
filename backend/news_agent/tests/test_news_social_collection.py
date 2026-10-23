import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from news_agent.collector import collect_once, initial_collection, write_snapshot
from news_agent.social import discussion_evidence
from scripts.export_news_social import export_rows


def test_x_export_is_read_only_and_never_exports_other_fields(tmp_path):
    path = tmp_path / "monitor.sqlite"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute("create table posts(post_json text,first_seen_at text,published_at text)")
        connection.execute("create table secrets(secret text)")
        connection.execute("insert into secrets values ('must-not-export')")
        connection.execute(
            "insert into posts values (?,?,?)",
            (
                json.dumps(
                    {
                        "text": "BTC discussion",
                        "url": "https://x.com/example/status/123",
                        "author": "example",
                        "published_at": now,
                        "private": "must-not-export",
                    }
                ),
                now,
                now,
            ),
        )
    before = path.read_bytes()
    rows = export_rows(path, "x")
    assert "must-not-export" not in json.dumps(rows)
    assert rows[0]["received_at"] == now
    assert path.read_bytes() == before


def test_social_adapter_rejects_stale_exports_wrong_domains_and_future_posts(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_SOCIAL_EXPORT_DIR", str(tmp_path))
    now = datetime.now(UTC)
    path = tmp_path / "x.jsonl"
    good = {
        "text": "BTC market discussion",
        "url": "https://x.com/user/status/123",
        "published_at": now.isoformat(),
        "author": "user",
    }
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                good,
                {**good, "url": "https://evil.example/x.com"},
                {**good, "published_at": (now + timedelta(days=1)).isoformat()},
            ]
        ),
        encoding="utf-8",
    )
    result = discussion_evidence("BTC", ["x"], now)
    assert len(result["posts"]) == 1
    assert result["posts"][0]["access"] == "collector_observation"
    os.utime(path, (now.timestamp() - 1000, now.timestamp() - 1000))
    assert not discussion_evidence("BTC", ["x"], now)["posts"]


def test_reddit_latest_removed_revision_does_not_resurrect_older_text(tmp_path):
    path = tmp_path / "evidence.sqlite"
    now = datetime.now(UTC)
    original = {
        "source_id": "reddit:t3_one",
        "canonical_url": "https://www.reddit.com/r/btc/comments/one",
        "event_at": now.isoformat(),
        "received_at": now.isoformat(),
        "raw": {"title": "BTC discussion", "selftext": "old text"},
        "observation": {},
        "provenance": {"mode": "rss"},
    }
    erased = {**original, "raw": None, "observation": {"deleted_or_removed": True}}
    with sqlite3.connect(path) as connection:
        connection.execute("create table evidence(envelope text,received_at text)")
        connection.execute("insert into evidence values (?,?)", (json.dumps(original), now.isoformat()))
        connection.execute(
            "insert into evidence values (?,?)", (json.dumps(erased), (now + timedelta(seconds=1)).isoformat())
        )
    assert not export_rows(path, "reddit")


def test_reddit_live_evidence_is_exported_but_replay_is_not(tmp_path):
    path = tmp_path / "reddit.sqlite"
    now = datetime.now(UTC).isoformat()
    live = {
        "source_id": "reddit:t3_one",
        "canonical_url": "https://www.reddit.com/r/btc/comments/one",
        "event_at": now,
        "received_at": now,
        "raw": {"title": "BTC discussion"},
        "provenance": {"mode": "rss"},
    }
    with sqlite3.connect(path) as connection:
        connection.execute("create table evidence(envelope text,received_at text)")
        connection.execute("insert into evidence values (?,?)", (json.dumps(live), now))
        connection.execute(
            "insert into evidence values (?,?)",
            (
                json.dumps(
                    {
                        **live,
                        "source_id": "reddit:t3_two",
                        "raw": {"title": "fabricated replay"},
                        "provenance": {"mode": "synthetic_replay"},
                    }
                ),
                now,
            ),
        )
    rows = export_rows(path, "reddit")
    assert len(rows) == 1 and "fabricated" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_pdf_targeted_page_and_phrase_share_download(monkeypatch):
    from types import SimpleNamespace

    from news_agent.tools import FetchResult, NewsResearchTools

    calls = []

    async def fetch(url, *args, **kwargs):
        calls.append(url)
        return FetchResult(url, url, "application/pdf", b"synthetic bounded PDF")

    pages = [
        SimpleNamespace(extract_text=lambda: "First page background. " * 500),
        SimpleNamespace(extract_text=lambda: "Second page official evidence. " * 100),
    ]
    monkeypatch.setattr(
        "news_agent.tools.PdfReader", lambda *args: SimpleNamespace(pages=pages, metadata={}, is_encrypted=False)
    )
    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    tools = NewsResearchTools(SimpleNamespace(allowed_domains=()))
    url = "https://example.com/report.pdf"
    initial = json.loads(await tools.read_news_article(url))
    assert "Second page" not in initial["article"]["text"]
    selected = json.loads(await tools.read_news_article(url, page=2))
    assert selected["ok"] and "Second page" in selected["article"]["text"]
    assert selected["article"]["selected_page"] == 2
    assert json.loads(await tools.read_news_article(url, find="Second page"))["ok"]
    assert not json.loads(await tools.read_news_article(url, page=99))["ok"]
    assert not json.loads(await tools.read_news_article(url, page=2, find="Second page"))["ok"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_background_collection_writes_both_assets_without_running_a_model(tmp_path, monkeypatch):
    calls = []

    async def curate(self, asset, topics):
        calls.append(asset)
        return json.dumps({"sources": [{"title": asset}], "source_health": []})

    monkeypatch.setattr("news_agent.collector.PublicSourceTools.curate_public_sources", curate)
    await collect_once(object(), tmp_path)
    assert sorted(calls) == ["BTC", "ETH"]
    for asset in calls:
        assert json.loads((tmp_path / "collected" / f"{asset}.json").read_text())["result"]["sources"]


@pytest.mark.parametrize("result", [[], {"sources": {}}, {"sources": ["not a card"]}])
def test_invalid_collection_snapshots_are_not_model_context(tmp_path, monkeypatch, result):
    monkeypatch.setenv("NEWS_STATE_DIR", str(tmp_path))
    now = datetime.now(UTC)
    write_snapshot(tmp_path / "collected" / "BTC.json", {"collected_at": now.isoformat(), "result": result})
    assert not initial_collection("BTC", now)
