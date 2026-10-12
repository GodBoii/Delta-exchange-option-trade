import json

from news_agent.report_quality import clean_report


def test_failed_reads_and_discovery_leads_cannot_support_claims():
    report = (
        "I'll investigate.## Summary\n\nVerified fact [official](https://official.example/final).\n\n"
        "An unverified claim [lead](https://news.google.com/rss/articles/lead).\n\n"
        "Blocked fact [blocked](https://blocked.example/story).\n\n## Sources\n\n"
        "- [official](https://official.example/final)\n- [blocked](https://blocked.example/story)"
    )
    tools = [
        {
            "tool_name": "read_news_article",
            "result": json.dumps(
                {
                    "ok": True,
                    "article": {
                        "requested_url": "https://official.example/request",
                        "final_url": "https://official.example/final",
                    },
                }
            ),
        },
        {"tool_name": "read_news_article", "result": json.dumps({"ok": False, "url": "https://blocked.example/story"})},
    ]
    result = clean_report(report, tools)
    assert result.markdown.startswith("## Summary")
    assert "Verified fact" in result.markdown
    assert "unverified claim" not in result.markdown
    assert "Blocked fact" not in result.markdown
    assert "blocked.example" not in result.markdown
    assert "Evidence gaps" in result.markdown
    assert result.omitted_blocks == 3


def test_calendar_urls_are_verified_but_other_feed_cards_are_only_leads():
    tools = [
        {
            "tool_name": "curate_public_sources",
            "result": json.dumps(
                {
                    "sources": [
                        {"kind": "calendar", "url": "https://bls.gov/calendar.ics"},
                        {"kind": "publisher", "url": "https://publisher.example/story"},
                    ]
                }
            ),
        }
    ]
    result = clean_report(
        "## Watch\n\nCPI [calendar](https://bls.gov/calendar.ics).\n\n"
        "Unsupported detail [publisher](https://publisher.example/story).",
        tools,
    )
    assert "CPI" in result.markdown
    assert "Unsupported detail" not in result.markdown


def test_dossier_redirects_and_canonical_tracking_urls_preserve_verified_claims():
    tools = [
        {
            "tool_name": "build_news_dossier",
            "result": json.dumps(
                {
                    "items": [
                        {
                            "ok": True,
                            "article": {
                                "requested_url": "https://a.example/story",
                                "final_url": "https://b.example/story",
                                "canonical_url": "https://b.example/canonical",
                            },
                        }
                    ]
                }
            ),
        }
    ]
    result = clean_report("## Summary\n\nFact [source](https://b.example/canonical?utm_source=test).", tools)
    assert result.omitted_blocks == 0
    assert "Fact" in result.markdown
