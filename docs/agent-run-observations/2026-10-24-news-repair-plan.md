# News regression diagnosis and repair plan

My 23 October change coupled research to an optional empty JSON schema. Three production runs returned
empty briefs without research, and the pipeline called them completed. Infrastructure and mocked dispatch
tests did not prove that the provider would choose tools. This was an application regression, not evidence
of a quiet news day. Strategy selection proceeded on missing evidence.

Other confirmed defects are RSS replacements dispatched by the old GDELT name, discarded specific gaps,
substring asset matching, and two legacy-account display-name failures. Additional failure boundaries
to check include cancelled reads, exhausted budgets, review/provider failure, stale leads, rejected claims,
unavailable calendars, recurring events and correction history. No system can anticipate every scenario;
unknown and failed research must remain explicit and must not authorize selection.

## Implementation sequence

1. Restore unconstrained Markdown/tool research. Keep structured extraction and semantic review separate
   from tool selection. Validate typed results locally in tool-free verification stages without provider
   JSON mode. Bound output tokens, prompt size, retries and stage deadlines.
2. Perform bounded source collection and original reads in code before inference, using the same tools,
   cache, clock and budget. Persist these actual executions with their origin. A model that skips tools
   cannot turn a run with no retrieved evidence into a successful news report.
3. Classify verified findings, researched-with-no-findings and no research separately. Block strategy
   selection deterministically without verified findings; preserve the news report and exact cause.
4. Dispatch RSS discovery replacements by format/kind, repair ETH/BTC token matching, show bounded safe
   gap details, and distinguish background discovery from current-run evidence.
5. Re-evaluate evidence identifiers, numerical checks, metadata approval, event identity and memory
   lifecycle. Preserve rejection reasons and avoid losing useful conditional interpretation unnecessarily.
6. Tolerate a missing legacy account display name without changing identity, capital or funding rules.
7. Add regressions for the actual empty HTTP response and actual parser path, mixed successes, all-source
   failure, cancellations, timeouts, quota exhaustion, invalid review output and downstream admission.
   Run the full backend/database suite, frontend regressions and typecheck.
8. Respect the user's revised test constraint: no further paid provider calls or end-to-end agent runs.
   Exercise all seven public tool interfaces directly for BTC and ETH. Test model dispatch and validation
   with HTTPX MockTransport and dummy credentials. Deploy after those checks, preserving active work,
   then verify deployed files, configuration and health without starting analyses.

## Verification boundary

Early live news-only attempts restored tool activity but timed out during extraction. Subsequent attempts
hit the provider's account credit limit. These checks led to smaller extraction inputs, bounded model
output, separate timeout classification and retained failure traces. They do not demonstrate final model
quality. The user then explicitly stopped paid testing; no later model calls are authorized by this plan.

Direct server tool probes retrieved four originals for each asset with zero model calls. Targeted passage
reads succeeded without another download. Collection remained partial, and some keyless search paths
returned no results. This proves fetching and cache behavior only. Future user-initiated production runs
must establish accepted-claim quality, relevance and runtime on the completed implementation.

Breaking-news triggers, pre-activation news refresh, monitoring, historical scoring and full archives
remain deferred. No recheck should be forced to approve merely to increase trading volume.
