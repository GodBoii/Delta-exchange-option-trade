# Options evidence at activation recheck

October 11, 2026. Main analysis selects a saved strategy and future entry using Spot
conditions and news. Activation recheck assesses whether that assigned options trade
should proceed using fresh option evidence. It can approve or cancel, not replace it.

## Implementation plan

1. Trace main inputs, tool registration, private snapshots, schedule validation,
   recheck context, confirmation and shared allocation before editing.
2. Remove live option overview, premium comparisons, IV scales and payoff preview
   from main analysis. Preserve saved strategy rules and private contract resolution.
3. Replace compact main preview with a schedule-only calculation and retain exact
   argument, saved-version and snapshot checks before selection.
4. Add fresh assigned-option evidence and a bound refresh tool to both recheck paths.
   Keep scheduling, sizing, allocation and execution in the existing backend.
5. Distinguish read-only calls from cancellation attempts. Failed evidence reads
   must not become approval; failed cancellation must not be reconfirmed.
6. Verify BTC/ETH, both input paths, schedule guards, approval/cancellation, stale
   evidence and offline replay. Update documentation, push focused commits and deploy.

## Runtime responsibilities

| Stage | Input and tools | Responsibility |
|---|---|---|
| Main | Spot, news, saved strategy catalogue, current-session constraints; `calculate_exit_time`, selection and follow-up scheduling | Choose a saved strategy and schedule, or wait/no trade |
| Recheck | Spot, original decision, actual assigned schedule, fresh selected-contract option preview; `preview_selected_strategy`, calculators, `drop_strategy` | Approve or cancel that one candidate |
| Backend | Private contracts and snapshots, saved rules, account policies and live execution data | Validate schedules, allocate capital, size, execute, reconcile and monitor |

The compact main path no longer exposes `preview_strategy`. `calculate_exit_time`
returns only `valid` and `schedule`, without premiums, spreads, IV, Greeks or P&L.
The main catalogue no longer has an indicative `comparison` or `comparisonBasis`.
Its numerical market input has no `options`, `holdingMoveScales` or option unit labels.
Futures measurements remain separate; Spot liquidity and realized volatility remain.

Saved option leg shapes, risk controls, user-authored limit prices and expiry constraints
remain necessary strategy metadata. Earlier reports are retained without rewriting their
historical option commentary. Agents must treat them as dated evidence, not current prices.

Main analysis still collects option data privately. The resolver needs listed expiries,
strikes and underlying prices to validate the saved rules. Snapshots preserve this data
for audit. No full chain is passed into either model's context.

## Recheck evidence and failure handling

`RecheckOptionTools` captures a copy of the assigned definition and schedule.
`preview_selected_strategy()` takes no arguments. It reads the asset's option catalogue,
resolves the saved leg rules and requests only those selected contracts. It cannot
schedule, change a strategy or inspect a different candidate.

The initial preview is collected before model invocation and stored in the decision
snapshot. Later preview results are retained through existing tool-call records.
The preview supplies normalized debit/credit, fees, selected quotes, multipliers, IV,
Greeks, gross expiry payoff, local value-change scenarios and advisory depth fills.
Movement scales use the actual assigned entry-to-exit duration. Compact overview history
is restricted to the assigned expiry dates, including previously scheduled legacy dates.

Selected quotes must satisfy the existing 30-second observation limit. Asset, option type,
strike and expiry must match the assigned trade; expired contracts fail. An initial read
failure fails the review before a model can approve it. A failed refresh invalidates
the evidence until a successful retry. If the model neither recovers evidence nor attempts
cancellation, the review fails and confirmation is not called.

Only a `drop_strategy` tool call enters cancellation verification. Read-only previews and
calculator calls allow normal confirmation when evidence is valid. A cancellation attempt
must have a persisted `strategy_dropped` outcome; a failed attempt is never approved.
Existing writer checks continue to bind the cancellation to the exact proposal and run.

Both recheck paths use sixteen tool calls. The configured model, low recheck reasoning,
seven-minute lead time, saved financial controls and account sizing remain unchanged.
The engine still reads execution data at entry; a recheck quote is not a guaranteed fill.

## Deployment

No database migration or frontend change is required. Both input modes use this stage split;
`CURATED_AGENT_INPUT_ENABLED=false` does not return options analysis to the main agent.
Update the repository on Ubuntu and rebuild the writer and analysis service:

```sh
git pull --ff-only origin main
docker compose --env-file .env.local --env-file backend/.env up -d --build --no-deps delta-exchange news-analyzer
```

Recheck cannot compensate for every unsuitable main selection by choosing another strategy.
This change relocates analysis and protects the approval path. It does not establish an
improvement in trading returns.

## Validation

The backend suite passed 563 tests. Seventy-three integration tests were skipped because
an isolated PostgreSQL test database was not configured. Focused lint, compilation and
diff checks passed. The suite used a temporary in-process `tests` package path to avoid
an installed package shadowing this repository's test helpers; no source change was needed.

Regression coverage includes both assets and both input modes, absence of live option
fields and tools in main analysis, schedule-only outputs, exact selection arguments,
saved-version and snapshot checks, successful shared publication, failed recalculation,
assigned-contract refresh, stale/missing/mismatched/nonfinite evidence, successful retry,
read-only calls followed by approval, and failed cancellation without reconfirmation.
The offline paired harness constructs both stages without invoking a model or scheduling trades.
