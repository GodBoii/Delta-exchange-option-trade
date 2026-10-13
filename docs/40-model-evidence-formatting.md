# Model evidence formatting

Main, activation recheck and news agents receive read-only evidence as Markdown
headings and lists. The renderer does not generate tables. Main and recheck
starting context uses the same renderer for schedules, chart context, prior
decisions, market summaries and assigned option evidence.

## Numbers and times

Analytical floats use two decimal places, rounded half up. When those places
would both be zero, precision extends through the first nonzero decimal.
Examples are `25.76391795730431` to `25.76`, `0.0011980816970479403` to `0.001`
and `-0.00004567` to `-0.00005`. Integers, identifiers, saved strategy definitions
and contract multipliers retain their original values. Source article text and
quoted numbers are preserved. Missing observations remain unavailable; zero and
false remain explicit.

Known epoch fields use the market services' millisecond schema. Known dated
fields with timezone offsets convert to IST, Asia/Kolkata, UTC+05:30. Display
includes the date, 24-hour time and fractional seconds when present. Date-only
or timezone-free source dates retain their original text. Durations and large
identifiers do not become timestamps. Chart labels use IST too; the underlying
time coordinates and gap detection remain unchanged.

For example, model-facing liquidity evidence is:

```markdown
## Market evidence

- previousLiquidityBucket:
  - start: 2026-10-13 10:20:00 IST
  - end: 2026-10-13 10:30:00 IST
  - spreadAverageBps: 0.001
  - spreadMaximumBps: 0.001
- recentTradeFlow:
  - firstTradeAt: 2026-10-13 10:20:00.106 IST
  - lastTradeAt: 2026-10-13 10:29:55.776 IST
```

Field names retain unit suffixes such as Percent, Bps, Usd and base-asset units.
Display formatting does not rescale prices, percentages, basis points or quantities.
Action tools still require timezone-aware ISO timestamps.

## Boundary and preservation

`news_agent.presentation` renders evidence without mutating it.
`EvidenceOpenRouter` converts a copy of recognized read-only tool content in
Agno's provider request serializer. `ResearchOpenRouter` inherits that behavior
and retains its existing provider failure handling.

Tool implementations and Agno tool execution records keep structured JSON.
Nested news searches, citation verification, research traces and cancellation
verification continue to parse their original results. Raw snapshots retain
their original numerical precision. Mutation confirmations, calculator results,
tool argument schemas and internal API payloads remain JSON. No database
migration, catalogue change, scheduling change or exchange action is involved.

The provider adapter covers the BTC/ETH market tools, strategy catalogue,
assigned option preview and all seven news research tools. Unknown tools and
non-JSON responses pass through unchanged. Add future evidence tools to the
adapter's explicit allowlist and extend the field schema for new timestamps.

## Verification

`tests/test_evidence_presentation.py` exercises rounding, tiny and negative
values, exact rules, missingness, IST day boundaries and fractional seconds.
It checks raw evidence retention through a real Agno function call, citation
verification and research tracing. Mock HTTP transports inspect the actual
synchronous and asynchronous provider requests without contacting a model.
Existing tests cover scheduling, evidence failure guards, both assets, chart
layout and offline input construction. Agno is pinned to 2.8.7; rerun provider
request tests when upgrading its message serialization.

Markdown is a readability change. Token savings are not established by these
tests and should be measured on actual provider usage before making a claim.
