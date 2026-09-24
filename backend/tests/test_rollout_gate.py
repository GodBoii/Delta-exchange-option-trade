import pytest

from scripts.rollout_gate import open_position_count


def test_rollout_gate_counts_live_positions():
    assert open_position_count({"result": [{"size": 0}, {"size": "-15"}, {"size": "8"}]}) == 2
    assert open_position_count({"result": []}) == 0
    with pytest.raises(ValueError, match="unavailable"):
        open_position_count({"result": None})
