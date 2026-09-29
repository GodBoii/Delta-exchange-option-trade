from scripts.model_replay import recorded_result
from scripts.score_model_replay import price_outcome, proxy_correct, regime_label, selection


def test_replay_matches_arguments_and_preserves_empty_results():
    calls = [{"tool_name": "select_strategy_and_time", "tool_args": {"strategy_ref": "S01"}, "result": ""}]
    assert recorded_result(calls, "select_strategy_and_time", {"strategy_ref": "S01"}) == ("", True)
    result, matched = recorded_result(calls, "select_strategy_and_time", {"strategy_ref": "S02"})
    assert not matched
    assert "unavailable_in_historical_record" in result


def test_read_only_tool_empty_arguments_match():
    assert recorded_result([{"name": "packet", "args": None, "result": "original"}], "packet", {}) == (
        "original",
        True,
    )


def test_outcomes_do_not_use_a_pre_decision_close_or_partial_horizon():
    candles = [{"time": t, "open": 100, "high": 100.1, "low": 99.9, "close": 100.1} for t in range(60, 3660, 60)]
    result = price_outcome(candles, 1, 1, 3660, 0.25)
    assert result["status"] == "complete"
    assert result["entry"] == 60
    assert result["entry_price"] == 100
    assert proxy_correct("sideways", result) is True
    assert price_outcome(candles, 1, 1, 3659, 0.25)["status"] == "immature"
    assert price_outcome(candles[:-1], 1, 1, 3660, 0.25)["status"] == "missing_candles"


def test_sideways_fails_if_price_round_trips_through_large_excursion():
    candles = [{"time": t, "open": 100, "high": 102, "low": 100, "close": 100} for t in range(60, 3660, 60)]
    result = price_outcome(candles, 1, 1, 3660, 0.25)
    assert result["direction"] == "sideways"
    assert proxy_correct("sideways", result) is False


def test_regime_parser_ignores_invalidation_and_missing_headings():
    assert regime_label("## Market regime\nBullish continuation.\n\n## Invalidation\nBearish below 100") == "bullish"
    assert regime_label("## Market regime\nBullish or bearish breakout possible") == "unscored"
    assert regime_label("Sideways market, but no explicit regime heading") == "unscored"
    assert regime_label("## Market regime\n**Bullish continuation.** The sideways score is 90%.") == "bullish"


def test_selection_keeps_only_the_matching_exit_preview():
    import json

    catalog = [
        {
            "tool_name": "show_available_strategy",
            "result": json.dumps(
                {
                    "strategies": [
                        {
                            "strategyRef": "S01",
                            "name": "Long call",
                            "definition": {"legs": [{"position": "buy", "optionType": "call"}]},
                        }
                    ]
                }
            ),
        }
    ]
    args = {
        "strategy_ref": "S01",
        "activation_time": "2026-09-29T05:45:00+05:30",
        "exit_choice": {"kind": "intraday", "hours": 7},
    }
    preview = {
        "tool_name": "calculate_exit_time",
        "tool_args": args,
        "result": '{"valid":true,"schedule":{"exitUtc":"2026-09-29T07:15:00Z"}}',
    }
    selected = selection([preview, {"tool_name": "select_strategy_and_time", "tool_args": args}], catalog)
    assert selected["directional_bias"] == "bullish"
    assert selected["planned_exit_utc"] == "2026-09-29T07:15:00Z"
    changed = {**args, "exit_choice": {"kind": "intraday", "hours": 11}}
    assert (
        selection([preview, {"tool_name": "select_strategy_and_time", "tool_args": changed}], catalog)[
            "planned_exit_utc"
        ]
        is None
    )
