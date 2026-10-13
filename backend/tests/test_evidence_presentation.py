"""Exercise display conversion at the real Agno provider boundary without trading or model calls."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from agno.models.message import Message
from agno.run.agent import RunOutput
from agno.tools.function import Function, FunctionCall

from automation_agent.evidence import dumps
from news_agent.failures import ResearchOpenRouter
from news_agent.model import EVIDENCE_TOOLS, EvidenceOpenRouter
from news_agent.pipeline import research_trace
from news_agent.presentation import format_number, format_time, markdown_evidence
from news_agent.report_quality import verified_evidence_urls


@pytest.mark.parametrize("value,expected", [
    (25.76391795730431, "25.76"), (76.987654, "76.99"), (0.0011980816970479403, "0.001"),
    (0.00004567, "0.00005"), (-0.001198, "-0.001"), (0.00999, "0.01"), (0.0, "0.00"),
    (-0.0, "0.00"), (Decimal("1.005"), "1.01"), (1e-12, "0.000000000001"),
    (1791867000000, "1791867000000"), (float("nan"), "Unavailable"), (float("inf"), "Unavailable"),
])
def test_number_display(value, expected):
    assert format_number(value) == expected


def test_dates_are_24_hour_ist_and_keep_event_precision():
    assert format_time(1791867000000, milliseconds=True) == "2026-10-13 10:20:00 IST"
    assert format_time(1791867600000, milliseconds=True) == "2026-10-13 10:30:00 IST"
    assert format_time(1791867000106, milliseconds=True) == "2026-10-13 10:20:00.106 IST"
    assert format_time(1791867595776, milliseconds=True) == "2026-10-13 10:29:55.776 IST"
    assert format_time("2026-10-13T23:59:58Z") == "2026-10-14 05:29:58 IST"
    assert format_time("2026-10-13T10:20:00+05:30") == "2026-10-13 10:20:00 IST"
    assert format_time(datetime(2026, 10, 13, tzinfo=UTC)) == "2026-10-13 05:30:00 IST"
    assert format_time("2026-10-13") is None
    assert format_time("unknown") is None
    assert format_time(0, milliseconds=True) is None
    assert format_time(True, milliseconds=True) is None


def test_schedule_labels_match_timezone_and_duplicate_times_are_removed():
    data = {"scheduledForUtc": "2026-10-13T04:50:00Z", "scheduledForIst": "2026-10-13T10:20:00+05:30"}
    text = markdown_evidence(data)
    assert text.count("2026-10-13 10:20:00 IST") == 1
    assert "scheduledForUtc" not in text
    assert "scheduledForIst: 2026-10-13 10:20:00 IST" in text
    assert "scheduledForIst: 2026-10-13 10:20:00 IST" in markdown_evidence({"scheduledForUtc": data["scheduledForUtc"]})


def test_lists_retain_units_missingness_zero_and_exact_saved_rules():
    evidence = {
        "previousLiquidityBucket": {"start": 1791867000000, "end": 1791867600000,
                                    "spreadAverageBps": 0.0011980816970479403},
        "recentTradeFlow": {"firstTradeAt": 1791867000106, "lastTradeAt": 1791867595776},
        "observedAt": 1791867000000, "id": "1791867000000", "start": 2,
        "gamma": 0.00004567, "bestBid": "25.76391795730431", "missing": None, "zero": 0.0,
        "complete": False, "empty": [], "multiplier": 0.00123456,
        "definition": {"limitPrice": 25.76391795730431, "quantity": "0.00123456", "enabled": True},
    }
    original = deepcopy(evidence)
    text = markdown_evidence(evidence)
    assert "- spreadAverageBps: 0.001" in text
    assert "- start: 2026-10-13 10:20:00 IST" in text
    assert "- end: 2026-10-13 10:30:00 IST" in text
    assert "- firstTradeAt: 2026-10-13 10:20:00.106 IST" in text
    assert "- lastTradeAt: 2026-10-13 10:29:55.776 IST" in text
    assert "- id: 1791867000000" in text and "- start: 2" in text
    assert "- gamma: 0.00005" in text and "- bestBid: 25.76" in text
    assert "- missing: Unavailable" in text and "- zero: 0.00" in text
    assert "- complete: false" in text and "- empty: None listed" in text
    assert "- multiplier: 0.00123456" in text
    assert "- limitPrice: 25.76391795730431" in text
    assert "- quantity: 0.00123456" in text
    assert not any(line.lstrip().startswith("|") for line in text.splitlines())
    assert evidence == original


@pytest.mark.parametrize("model_type", [EvidenceOpenRouter, ResearchOpenRouter])
@pytest.mark.parametrize("tool_name", list(EVIDENCE_TOOLS))
def test_provider_formats_read_only_evidence_without_mutating_records(model_type, tool_name):
    raw = json.dumps({"value": 25.76391795730431, "capturedAt": 1791867000000})
    message = Message(role="tool", tool_name=tool_name, tool_call_id="call_evidence", content=raw)
    model = model_type(api_key="test-unused")
    payload = model._format_all_messages([message])[0]
    assert payload["content"].startswith("## " + EVIDENCE_TOOLS[tool_name])
    assert "- value: 25.76" in payload["content"]
    assert "- capturedAt: 2026-10-13 10:20:00 IST" in payload["content"]
    assert payload["tool_call_id"] == "call_evidence"
    assert message.content == raw


@pytest.mark.parametrize("tool_name", [
    "select_strategy_and_time", "scheduled_next_agent_run", "drop_strategy", "multiply",
])
def test_actions_and_calculators_keep_machine_results(tool_name):
    raw = json.dumps({"success": True, "value": 25.76391795730431})
    message = Message(role="tool", tool_name=tool_name, tool_call_id="call_action", content=raw)
    assert EvidenceOpenRouter()._format_message(message)["content"] == raw


def test_original_articles_and_citation_provenance_survive_presentation():
    article = {
        "ok": True, "article": {
            "final_url": "https://example.com/article", "published_at": "2026-10-13T04:50:00Z",
            "text": "Original claim: 25.76391795730431.\nSecond line with 0.0011980816970479403.",
        },
    }
    raw = json.dumps(article)
    function = Function.from_callable(lambda: raw, name="read_news_article")
    call = FunctionCall(function=function, call_id="call_article")
    model = ResearchOpenRouter(api_key="test-unused")
    results = []
    events = list(model.run_function_call(call, results))
    execution = next(e for e in events if e.tool_executions and e.tool_executions[0].result).tool_executions[0]
    payload = model._format_all_messages(results)[0]
    assert article["article"]["text"].splitlines()[0] in payload["content"]
    assert "2026-10-13 10:20:00 IST" in payload["content"]
    assert execution.result == raw
    assert verified_evidence_urls([execution]) == {"https://example.com/article"}
    trace = research_trace(RunOutput(tools=[execution]))
    assert trace[0]["result"]["published_at"] == "2026-10-13T04:50:00Z"
    assert trace[0]["result"]["text_chars"] == len(article["article"]["text"])


def test_unsupported_content_and_provider_reasoning_remain_intact():
    model = EvidenceOpenRouter()
    for content in ("Unavailable", "[invalid JSON", "25.76391795730431"):
        message = Message(role="tool", tool_name="get_btc_market_packet", content=content)
        assert model._format_message(message)["content"] == content
    message = Message(role="assistant", content="Decision", provider_data={"reasoning_details": [{"text": "Reason"}]})
    assert model._format_message(message)["reasoning_details"] == message.provider_data["reasoning_details"]


def test_machine_serializer_preserves_precision_and_rejects_nonfinite_values():
    raw = {"value": 25.76391795730431}
    assert json.loads(dumps(raw)) == raw
    with pytest.raises(ValueError):
        dumps({"value": float("nan")})


@pytest.mark.parametrize("async_request", [False, True])
async def test_real_provider_request_sends_markdown_and_keeps_tool_schema(async_request):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done"}, "finish_reason": "stop"}],
        })

    raw = json.dumps({"lastPrice": 25.76391795730431, "lastEventAt": 1791867000106})
    messages = [
        Message(role="assistant", content=None, tool_calls=[{
            "id": "call_market", "type": "function", "function": {"name": "get_btc_market_packet", "arguments": "{}"},
        }]),
        Message(role="tool", tool_name="get_btc_market_packet", tool_call_id="call_market", content=raw),
    ]
    schema = [{"type": "function", "function": {
        "name": "get_btc_market_packet", "parameters": {"type": "object", "properties": {}},
    }}]
    transport = httpx.MockTransport(respond)
    if async_request:
        async with httpx.AsyncClient(transport=transport) as client:
            model = ResearchOpenRouter(api_key="test-unused", http_client=client, base_url="http://provider.test")
            await model.ainvoke(messages, Message(role="assistant"), tools=schema)
    else:
        with httpx.Client(transport=transport) as client:
            model = EvidenceOpenRouter(api_key="test-unused", http_client=client, base_url="http://provider.test")
            model.invoke(messages, Message(role="assistant"), tools=schema)
    assert len(requests) == 1
    content = requests[0]["messages"][-1]["content"]
    assert "- lastPrice: 25.76" in content
    assert "- lastEventAt: 2026-10-13 10:20:00.106 IST" in content
    assert requests[0]["tools"] == schema
    assert messages[-1].content == raw
