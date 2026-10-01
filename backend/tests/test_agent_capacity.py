from decimal import Decimal

import pytest

from app.capital import CapitalPolicy, has_available_capital_slot


@pytest.mark.parametrize(
    "mode,maximum",
    [("full_balance", 1), ("half_balance", 2), ("one_third_balance", 3), ("one_quarter_balance", 4)],
)
def test_percentage_slots_allow_last_entry_then_block(mode, maximum):
    policy = CapitalPolicy(mode)
    budget = Decimal("120") / maximum
    assert has_available_capital_slot(
        budget, Decimal("120"), policy, list(range(1, maximum)), budget * (maximum - 1)
    )
    assert not has_available_capital_slot(
        budget, Decimal("120"), policy, list(range(1, maximum + 1)), Decimal("120")
    )


@pytest.mark.parametrize(
    "available,total,amount,slots,reserved,expected",
    [
        (25, 100, 25, [1, 2, 3], 75, True),
        (25, 100, 25, [1, 2, 3, 4], 100, False),
        (10, 100, 25, [1], 90, True),
        (50, 100, 200, [], 0, True),
        (0, 100, 25, [], 0, False),
        (25, 0, 25, [], 0, False),
        (25, 100, 25, [], 80, False),
        (25, 100, 25, [7, 8, 9, 10], 40, False),
        (1, 1000, 1, list(range(1, 101)), 100, False),
    ],
)
def test_fixed_budget_uses_existing_entry_limits(available, total, amount, slots, reserved, expected):
    assert has_available_capital_slot(
        Decimal(available), Decimal(total), CapitalPolicy("fixed_amount", Decimal(amount)), slots, Decimal(reserved)
    ) is expected


def test_released_slot_becomes_available_and_alias_budget_still_counts():
    policy = CapitalPolicy("one_quarter_balance")
    assert has_available_capital_slot(Decimal("25"), Decimal("100"), policy, [1, 3, 4], Decimal("75"))
    assert not has_available_capital_slot(Decimal("25"), Decimal("100"), policy, [1], Decimal("100"))


def test_existing_allocations_can_block_a_changed_capital_policy():
    assert not has_available_capital_slot(
        Decimal("100"), Decimal("100"), CapitalPolicy("half_balance"), [4], Decimal("75")
    )
