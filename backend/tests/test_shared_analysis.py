from app.shared_analysis import choose_setup


def test_private_copy_cannot_override_ranked_builtin():
    builtin = {"id": "builtin", "version": 2, "user_id": None, "enabled_for_ai": True}
    private = {**builtin, "id": "private", "user_id": "account"}
    assert choose_setup([{"id": "builtin", "version": 2}], [private, builtin]) == builtin
    assert choose_setup([{"id": "private", "version": 2}], [private, builtin]) is None


def test_ranking_skips_changed_disabled_and_deleted_definitions():
    catalog = [
        {"id": "changed", "version": 3, "user_id": None, "enabled_for_ai": True},
        {"id": "disabled", "version": 2, "user_id": None, "enabled_for_ai": False},
        {"id": "deleted", "version": 2, "user_id": None, "enabled_for_ai": True, "deleted": True},
        {"id": "eligible", "version": 2, "user_id": None, "enabled_for_ai": True},
    ]
    candidates = [{"id": row["id"], "version": 2} for row in catalog]
    assert choose_setup(candidates, catalog) == catalog[-1]
    assert choose_setup(candidates[:-1], catalog) is None
