# Social evidence JSONL version 1

`python -m reddit_monitor export` emits one JSON object per retained content
revision. It contains public evidence only. It never exports local config,
approval references, network headers or credentials. This is a local interchange
format; it does not grant permission to redistribute Reddit data.

| Field | Contract |
| --- | --- |
| `schema_version` | `social-evidence/1` |
| `source` | `reddit`, future adapters can supply `x` or another platform |
| `native_id` | Exact platform ID, such as `t1_abc` or `t3_xyz`, always a string |
| `source_id` | Namespaced identity, such as `reddit:t3_xyz` |
| `kind` | `post` or `comment` |
| `event_at` | UTC publication time or null when unavailable |
| `event_time_kind` | `publication` or `unknown`; updated time is never relabeled publication |
| `source_updated_at` | Source feed update time, separate from publication and receipt |
| `received_at` | Actual local page-response completion time; replay explicitly supplies synthetic time |
| `last_seen_at` | Latest receipt of this exact raw variant |
| `canonical_url` | Validated Reddit permalink or null for erased evidence |
| `community` | Source community name and native ID when provided |
| `author` | Public source name and native ID when provided; null after erasure |
| `context` | Native thread, parent, crosspost and quote IDs; missing data stays unknown; completeness flag stays false |
| `screening` | Stored version, relevance label, keyword evidence, unverified identity candidates and unassessed direction |
| `observation` | Source edit/deletion observations, not an inferred time of deletion |
| `observation_class` | `baseline`, `discovery` or `revision`; discovery may still be stale |
| `research_event` | Local freshness eligibility metadata, never a notification or trading instruction |
| `provenance` | Acquisition mode, endpoint, response/fixture digest, availability and timestamp-source flags |
| `raw` | Original Listing data or extracted Atom fields including serialized raw entry; null after deletion |

For native Listing data, `created_utc`, `edited`, source IDs, and original thread
references survive in `raw`. For Atom, publication is populated only from
`published`, while `updated` survives independently. A response hash proves which
response bytes the adapter processed; raw entry serialization preserves XML
content but does not promise byte-for-byte page reconstruction.

Storage identity is native ID plus SHA-256 of the raw variant. Identical reads do
not append duplicate revisions. Receipt timestamps are observations, never evidence
that a source event was immediately visible. Missing fields and incomplete context
must remain unknown when exports are combined.

A future X adapter should read X's existing export and map its original numeric
string into `native_id`, set `source=x`, preserve its `first_seen_at` receipt
semantics, and retain reply/quote/repost context and original screening version.
It must not mutate X's evidence or claim X's batch receipt is per-post arrival.
No such adapter or shared database is implemented here.

Export reads bounded batches without holding a long read transaction. Concurrent
edits or removals can change the result during export. Reconcile again before use.

Downstream consumers should join by `source_id`, compare revisions explicitly,
keep platform author namespaces separate, and preserve provenance. Exports are
snapshots, not an erasure stream. Consumers must reconcile deletions and expired
retention against the source store. Previously saved files require separate
erasure. Never correlate Reddit authors with off-platform identities.
