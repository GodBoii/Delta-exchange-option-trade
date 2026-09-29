# Historical model replay

Compare GPT-6.1 Sol at medium reasoning with the original DeepSeek v4.1 Flash
and MiMo v2.6 Pro decisions. The original models are identified from each saved
Agno session, not from today's configured default.

## Export and prepare

Run the exporter in the analysis-service environment. It uses explicit read-only,
repeatable-read transactions and exports only analysis reports, decision sessions
and PNG charts. It does not export exchange credentials or call trading tools.

```powershell
python -m scripts.export_model_replay /private/evidence.json --hours 72
```

Prepare cases on a machine with this Git repository. Run from the backend directory.

```powershell
python -m scripts.model_replay prepare /private/evidence.json /private/cases.json --repo /path/to/repository
```

The original system and user messages stop before the first original assistant
response. Stored chart bytes are restored to the user message in their recorded
order. GPT receives neither the baseline's answer nor future market candles.
Each input has a SHA-256 digest.

Original tool schemas were not persisted. Preparation reconstructs them from Git
at each run's timestamp. If recorded arguments prove that deployment preceded
the matching commit, preparation uses the matching current callable definition
and lists that correction in the case metadata. This approximation limits claims
of exact original-environment fidelity.

## Run GPT

Use a funded `OPENROUTER_API_KEY` in the execution environment. The runner always
requests `openai/gpt-6.1-sol` with medium reasoning and requires provider parameter
support. It verifies the returned model ID and stores provider usage and latency.
It excludes private reasoning content; written reports, tool arguments and their
explicit reasoning summaries are the analysis evidence.

```powershell
python -m scripts.model_replay run /private/cases.json /private/results --workers 3
python -m scripts.model_replay run /private/cases.json /private/results --workers 3 --retry-failed
```

Results are resumable by case ID and input digest. Retry preserves successful
results. Keep separate directories for different input datasets. The spending
limit belongs to the configured provider key; the runner does not change it.

The dispatcher only matches saved tool names and exact arguments. It never
imports or executes the live trading implementations. Calls without a recorded
match receive an explicit unavailable result. Returning an original scheduling
success for a different strategy or timestamp would falsify the comparison.

This design measures behavior in a restricted historical replay. An unconstrained
simulation needs historical tool snapshots supporting every valid alternative
strategy, exit and timestamp. That data is not implied by a saved transcript.

## Compare with actual prices

```powershell
python -m scripts.score_model_replay /private/cases.json /private/results /private/comparison
```

The report includes paired regime checks, proposed strategies and entry times,
written reports, provider costs and durations. JSON retains per-case results and
CSV provides a compact index. Market candles are fetched once from Delta's
public BTCUSD and ETHUSD history API and cached privately.

Scoring uses complete one-minute candles over 1, 3 and 6 hours. Snapshot checks
start at the next minute open. Proposed-entry checks start at the requested
minute for minute-aligned entries. Horizons after the frozen export cutoff or
with missing minutes are excluded. BTC neutral movement is 0.25%; ETH is 0.50%.
A sideways match also requires the largest excursion to remain within twice
the threshold. These arbitrary thresholds define a price proxy, not universal
prediction accuracy. The regime parser only reads the leading regime heading.

No trade is an abstention from a trade-return score. A bullish description of
the daily chart is not necessarily a prediction of the next hour. Directional
strategy checks only score one-sided exposures; two-sided volatility structures
need option-premium and volatility data. Underlying movement never establishes
options profit, fills, fees, slippage or the actual risk exit.

DeepSeek and MiMo ran on different timestamps. Compare GPT with each baseline
on matched cases; their separate totals cannot rank DeepSeek directly against
MiMo. Original durations include original agent/tool overhead; replay durations
include model calls and dispatcher overhead. They are observed times, not a
controlled speed benchmark.

Private exports and reports belong under the ignored `data/` directory or
another private location. Only the evaluation code and this runbook belong in Git.
