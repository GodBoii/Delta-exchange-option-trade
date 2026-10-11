from decimal import Decimal as D

import pytest

from scripts.option_replay import Candle, Leg, Policy
from scripts.otm_replay import continuous_replay, mark_entry_legs, shifted_contracts


def leg(kind="call"):
    return Leg(f"{kind}-100", "sell", kind, D(100), D(2), D(".01"), D(5), "2026-10-11T12:00:00Z", D(".0001"), D(".035"))


def snapshot():
    return [
        {
            "symbol": f"{kind}-{strike}",
            "type": f"{kind}_options",
            "strike": strike,
            "expiry": "2026-10-11T12:00:00Z",
            "contractValue": 0.01,
            "observedAt": 59000,
            "receivedAt": 59500,
            "takerFeeRate": 0.0001,
        }
        for kind in ("call", "put")
        for strike in (80, 90, 100, 110, 120)
    ]


def bar(start, price):
    return Candle(start, D(price), D(price), D(price), D(price))


def test_otm_pair_moves_outward_on_each_side_and_retains_expiry():
    rows = shifted_contracts([leg(), leg("put")], {60000: snapshot()}, entry_at=61, spot=D(101), steps=2)
    assert [row["strike"] for row in rows] == [120, 80]


def test_future_and_stale_chains_are_unavailable():
    assert shifted_contracts([leg()], {62000: snapshot()}, entry_at=61, spot=D(100), steps=1) is None
    assert shifted_contracts([leg()], {60000: snapshot()}, entry_at=661, spot=D(100), steps=1) is None


def test_future_received_quote_cannot_establish_a_listing():
    rows = [{**r, "receivedAt": 62000} for r in snapshot()]
    assert shifted_contracts([leg()], {60000: rows}, entry_at=61, spot=D(100), steps=1) is None


def test_chain_edge_and_strike_still_inside_spot_are_unavailable():
    assert shifted_contracts([leg()], {60000: snapshot()[:3]}, entry_at=61, spot=D(100), steps=1) is None
    assert shifted_contracts([leg()], {60000: snapshot()}, entry_at=61, spot=D(111), steps=1) is None


def test_entry_uses_completed_mark_and_keeps_contract_quantity():
    selected = shifted_contracts([leg()], {60000: snapshot()}, entry_at=61, spot=D(100), steps=1)
    result = mark_entry_legs(
        [leg()], selected, {"call-110": {0: bar(0, "3"), 60: bar(60, "99")}}, entry_at=61, emergency_percent=D(300)
    )
    assert result[0].entry == D(3)
    assert result[0].stop_price == D(12)
    assert result[0].contracts == D(2)


def test_missing_pre_entry_mark_does_not_become_zero_cost():
    selected = shifted_contracts([leg()], {60000: snapshot()}, entry_at=61, spot=D(100), steps=1)
    assert mark_entry_legs([leg()], selected, {}, entry_at=61, emergency_percent=None) is None


def test_missing_stop_minute_invalidates_later_exit():
    policy = Policy("combined_premium", "net_credit", D(100), D(50), 180)
    series = {"call-100": {0: bar(0, "5"), 120: bar(120, "11")}}
    assert (
        continuous_replay(
            [leg()], series, {0: bar(0, "100"), 120: bar(120, "100")}, policy, entry_at=1, intrabar_emergency=True
        )
        is None
    )


def test_multiplier_mismatch_is_rejected():
    rows = [{**r, "contractValue": 0.1} for r in snapshot()]
    with pytest.raises(ValueError, match="multiplier"):
        shifted_contracts([leg()], {60000: rows}, entry_at=61, spot=D(100), steps=1)
