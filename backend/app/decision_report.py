"""Render a model report using the action committed by the scheduling store."""

import re


def committed_action_text(outcome: str, *, shared: bool) -> str:
    return {
        "strategy_selected": (
            "A shared strategy proposal was recorded. Account entry still requires recheck and allocation."
            if shared else "A strategy was scheduled for this account. Entry still requires the activation recheck."
        ),
        "wait_and_run_again": "A follow-up review was scheduled or an existing review was reused.",
        "no_trade_for_current_window": "No strategy was scheduled during this review.",
    }.get(outcome, "No trading action was confirmed during this review.")


def replace_model_decision(outcome: str, report: str, *, shared: bool) -> str:
    """Replace the model's free-text decision with the committed outcome."""
    decision = f"## Decision\n\n{committed_action_text(outcome, shared=shared)}\n\n"
    pattern = re.compile(r"^## Decision\b[^\n]*\n.*?(?=^## |\Z)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if pattern.search(report):
        return pattern.sub(lambda _: decision, report, count=1).strip()
    return f"{report.strip()}\n\n{decision}".strip()
