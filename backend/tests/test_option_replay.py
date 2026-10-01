from decimal import Decimal as D

import pytest

from scripts.option_replay import Candle, Leg, Policy, latest_candle, replay_policy, short_pair_breakevens


def leg(symbol="call", kind="call", strike="100", entry="10", stop=None, side="sell"):
    return Leg(symbol, side, kind, D(strike), D("2"), D("0.01"), D(entry), "2026-10-01", D("0.0001"), D(".035"), stop)


def bar(at, value, high=None):
    value = D(value)
    return Candle(at, value, D(high) if high else value, value, value)


def test_marks_never_read_a_future_candle():
    bars = {0: bar(0, "8"), 60: bar(60, "100")}
    assert latest_candle(bars, 61).close == 8
    assert latest_candle(bars, 59) is None
    assert latest_candle(bars, 120).close == 100


def test_sideways_round_trip_can_trigger_emergency_loss():
    legs = [leg(stop=D("27")), leg("put", "put", stop=D("27"))]
    series = {"call": {0: bar(0, "28"), 60: bar(60, "5")}, "put": {0: bar(0, "1"), 60: bar(60, "5")}}
    spot = {0: bar(0, "125"), 60: bar(60, "100")}
    policy = Policy("strategy_level", "net_credit", D(100), D(50), 120)
    protected = replay_policy(legs, series, spot, policy, entry_at=0, include_brackets=True)
    assert protected.reason == "external_leg_exit"
    assert protected.at == 60 and protected.gross == D("-.18")
    held = replay_policy(legs, series, spot, policy, entry_at=0, include_brackets=False, financial_triggers=False)
    assert held.gross == D(".20") and held.at == 120


def test_flat_underlying_can_lose_when_both_option_marks_expand():
    legs = [leg(), leg("put", "put")]
    series = {"call": {0: bar(0, "15")}, "put": {0: bar(0, "15")}}
    policy = Policy("strategy_level", "net_credit", D(100), D(50), 60)
    result = replay_policy(legs, series, {0: bar(0, "100")}, policy, entry_at=0, include_brackets=False)
    assert result.gross == D("-.20")
    assert result.reason == "scheduled_exit"


def test_intrabar_stop_crossing_is_flagged_without_inventing_its_fill():
    leg_ = leg(stop=D("27"))
    series = {"call": {0: bar(0, "12", "30")}}
    policy = Policy("strategy_level", "net_credit", D(100), D(50), 60)
    result = replay_policy([leg_], series, {0: bar(0, "100")}, policy, entry_at=0, include_brackets=True)
    assert result.reason == "scheduled_exit"
    assert result.observed_intrabar_emergency_crossings == 1


def test_missing_leg_bars_do_not_produce_free_profit():
    policy = Policy("strategy_level", "net_credit", D(100), D(50), 60)
    assert replay_policy([leg()], {}, {0: bar(0, "100")}, policy, entry_at=0, include_brackets=True) is None


def test_expiry_breakevens_include_both_premiums_and_contract_units():
    legs = [leg(strike="104", entry="2"), leg("put", "put", strike="96", entry="3")]
    assert short_pair_breakevens(legs) == (D("91"), D("109"))


def test_fees_are_based_on_filled_contract_units_and_include_gst_once():
    assert leg().estimated_close_fee(D("10"), D("100")) == D(".000236")


def test_invalid_ohlc_is_rejected():
    with pytest.raises(ValueError, match="OHLC"):
        Candle(0, D("10"), D("9"), D("8"), D("10"))
