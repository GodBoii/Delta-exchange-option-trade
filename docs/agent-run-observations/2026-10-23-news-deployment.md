# Structured news deployment, 23 October 2026

The agreed news implementation was pushed to main, pulled on ubuntu-server, rebuilt and started at
approximately 17:12 through 17:13 IST. Application code at deployment is commit 0610f8b.
The running analyzer's intelligence.py SHA-256 matches the checked-out Git version:
448a3417e9453a3ae858d370ac5dd2da46a7bbeed5eec3f9faa68829d00e6fe6.

## Implemented scope

- Existing stage-one fixes are now deployed, including market capture after news, shared search cache,
  reserved verification time, topic-aware discovery, coverage disclosure and targeted article reads.
- Structured research returns typed events and stable evidence IDs directly. A separate tool-free model
  pass reviews factual entailment and event metadata. This avoids an extra extraction request in the normal
  production path. Legacy string results retain a bounded extraction compatibility path.
- Exact-passage checks, stated-number checks, unique approval verdicts, metadata approval and fail-closed
  rendering prevent an unreviewed draft from becoming the final report. Model review is not a truth oracle.
  Output text is escaped; source links are generated from collected provenance rather than model markup.
- Migration 016 adds public event memory. It shares MACRO events across assets, tracks first/last receipt,
  distinguishes release dates and retains corrections/retractions. Conservative story grouping tracks
  domains and source counts without claiming that syndicated reports are independent corroboration.
- Background collection warms common public-source caches and supplies recent discovery leads to runs.
  It does not schedule analyses or trades. Both asset research paths keep separate interpretations.
- Public X exports are read from the existing collector using a separate network-disabled export service.
  Only whitelisted public fields reach the analyzer. No account/session credential database is mounted in
  the analyzer. Stale snapshots, invalid URLs, future posts, erased Reddit revisions and synthetic replay
  are rejected. Social observations are excluded from long-lived shared event memory.
- The Reddit reader/export adapter is implemented, but no live Reddit store is configured in deployment.
  This is an input-availability limit; live Reddit coverage must not be inferred from adapter availability.
- Coverage expands to 35 baseline connectors per asset, with an additional bounded topic query when used.
  Added coverage includes BIS RSS 1.0, English/Japanese Bank of Japan, institutional-disclosure discovery,
  infrastructure discovery and regional macro discovery. HTML language metadata and reviewed translation
  preserve original-language passages. Discovery is explicitly distinct from successful original reads.
- Repeatedly failing Binance/GDELT/Polymarket direct connectors have labelled public discovery replacements
  with original endpoint/status metadata. Direct attempts can be restored explicitly for diagnosis.
  Quiet official feeds are not treated as broken merely because their latest release is older than 48 hours.
- Retained PDF pages support phrase and page reads without repeated downloads, within twelve pages and
  128,000 retained characters; responses remain bounded to 8,000 characters. No OCR is claimed.
- Release comparisons retain actual/forecast/previous/revision values but calculate surprises only with
  compatible explicit units and reference periods. Missing or mismatched context stays unknown.

## Verification

- Final isolated Linux/PostgreSQL suite: 1,074 passed and one skipped, with no failures.
- The skip was reproduced with its reason: the existing future-entry session-policy fixture deliberately
  skips when the real clock is too close to expiry. Both ordinary and ETH catalog test databases were supplied.
- After the final literal-rendering guard: all 99 news tests passed locally, including real Agno structured
  output/tool dispatch with mocked provider responses. No paid provider request was made by those tests.
- Frontend: all 97 tests passed; TypeScript typecheck passed.
- Ruff passed across app, automation_agent, news_agent, news_analyzer and the new exporter/tests.
- Compose configuration validation and Python compilation passed.
- Disposable test databases/network used fake credentials and read-only source mounts, without production data.

## Deployment and smoke results

No analyses or active strategies were running at the deployment guard. The writer image was rebuilt,
migration 016 was applied, and Delta-exchange was recreated and became healthy. bitcoin-agent was rebuilt
and recreated and became healthy. New collector/export services are running with zero restarts; they have
no configured Docker health probe, so running state alone is not described as a health-check result.
Existing unrelated containers and environment files were preserved.

The analyzer reports structured mode enabled, MiMo selected, Brief output schema and seven research tools.
Its analysis database role can select/insert/update/delete the event-memory table. The first inspected
snapshots were about 44 seconds old, with 107 BTC cards and 103 ETH cards from 35 connectors each.
These are discovery cards, not counts of independently verified facts. The X export contained 412 public
records in its collection window and was about 57 seconds old; topic/time filtering limits what a run uses.
The first five-minute collector/export log check contained no Python tracebacks.

Both market-data containers already reported unhealthy before this deployment. Their underlying feed
behavior was not repaired as part of the user's requested focus on news implementation. Writer/analyzer
health does not establish that unrelated upstream market feeds are healthy.

## Production assessment boundary

No manual analysis, approval, order or trade was triggered for this deployment. The user plans to wait
for two or three normal analysis runs before requesting a production review. Therefore improved model
judgment, end-to-end latency, claim acceptance rates and practical report quality remain unmeasured in
production. Source collection, export delivery, schema/configuration and service readiness were verified.

Breaking-news triggers, pre-activation news refresh, operational monitoring, historical evaluation and
complete reproducible evidence archives remain deferred as requested. Existing analysis reports remain
historical reports rather than a live social-deletion mirror.
