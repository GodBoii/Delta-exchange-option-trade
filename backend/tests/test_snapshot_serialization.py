import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from automation_agent import tools


def test_snapshot_persists_nested_parent_timestamp(monkeypatch):
    captured = {}
    instant = datetime(2026, 9, 15, 19, tzinfo=UTC)
    account = {"previousRun": {"scheduledFor": instant, "finalResponse": "complete report"}}
    market = {"price": 123.5, "samples": [None, 0]}

    def request(path, args, **kwargs):
        assert path == "runtimeAutomation:saveSnapshot"
        captured.update(market=json.loads(args["marketJson"]), account=json.loads(args["accountJson"]))
        return "snapshot"

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=request))
    identity = "11111111-1111-4111-8111-111111111111"
    assert tools.save_market_snapshot(SimpleNamespace(), user_id=identity, agent_run_id=identity,
                                      market_packet=market, account_context=account) == "snapshot"
    assert captured == {"market": market, "account": {"previousRun": {
        "scheduledFor": instant.isoformat(), "finalResponse": "complete report"}}}
    assert account["previousRun"]["scheduledFor"] == instant


@pytest.mark.parametrize("value", [datetime(2026, 9, 15), object(), float("nan")])
def test_snapshot_rejects_ambiguous_or_unsupported_values(value):
    with pytest.raises((ValueError, TypeError)):
        tools.snapshot_json({"value": value})
