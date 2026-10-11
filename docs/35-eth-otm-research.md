# ETH ATM and OTM research

Use `backend/scripts/otm_replay.py` with the existing offline option replay
mathematics to compare frozen ETH entries at the original ATM strike and one
or two listed steps outward. This is research code; it cannot submit orders or
modify the shared strategy library.

The October 11 investigation and account evidence are under the Git-ignored
`data/eth-otm-2026-10-11/` directory. Do not commit account exports, trade-level
results, or private reports. The private `data/export_eth_research.py` and
`data/run_eth_otm.py` scripts record the export and calculation steps.

## Comparison rules

- Use settled ledger rows for actual performance. Report actual fills separately
  from counterfactual option marks.
- Keep entry times, expiries, filled contract quantities, saved deadlines, and
  saved combined stop/target percentages unchanged across the strike variants.
- Select outward strikes from one historical chain snapshot available before
  entry. The original filled ATM contract must appear in that chain. Reject a
  shifted call below spot or a shifted put above spot.
- A snapshot up to ten minutes old establishes listing availability only. Never
  call its bid/ask an executable entry-time quote.
- Price hypothetical entries from completed option-mark bars available before
  entry, with a maximum age of 120 seconds. Reject missing or zero entry prices.
- Evaluate production strategy-level risk mathematics at completed minute
  closes. Run a separate emergency variant that detects short-leg stop crossings
  inside complete bars and values the exit at the completed close. Neither variant
  promises a stop-threshold fill.
- Reject missing option or underlying minutes before the hypothetical exit.
  Missing later bars after a simulated exit do not invalidate that exit.
- Estimate commissions from contract units and option metadata. State the
  historical premium-cap and GST assumptions. Include execution-cost sensitivity.
- Also compare marks at the recorded actual exit time to isolate strike choice
  from hypothetical changes in exit timing. Use only bars completed by that time.
- Compare fixed quantities first. Re-sizing to preserve entry credit increases
  OTM quantity and can erase much of the apparent dollar-risk reduction. Any
  fractional scaling is an analytical sensitivity, not an executable order plan.

This is a fixed-entry counterfactual, not a self-financing backtest. Later capital,
trade capacity, and agent decisions remain frozen. Account copies of a shared
decision are not independent strategy observations. ETH directional trades
without historical chain evidence must remain untested, even if their actual
results are known.

## Validation

From `backend`, run:

```text
python -m pytest tests/test_otm_replay.py tests/test_option_replay.py -q
python -m ruff check scripts/otm_replay.py tests/test_otm_replay.py
```

The tests cover outward call/put strikes, stale and future observations, a quote
received after entry, chain edges, pre-entry mark selection, missing stop minutes,
preserved contract quantity, and mismatched multipliers.
