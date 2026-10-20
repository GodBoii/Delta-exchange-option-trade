# Access research, 20 October 2026

## Decision

Use synthetic replay now. Keep a keyless RSS adapter ready for a specifically
permitted route. Do not run it for research without Reddit's approval, infer
permission from a 200 response, or change identities when blocked. No paid scraper
or API-key subscription was added. The user's preference is keyless access.

## Current primary documentation

- [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy),
  updated 5 October 2026, requires approval for API access and routes research to
  RFR. It also bars research collected outside that program and forbids masking
  purpose or circumventing limits. A public endpoint is not approval for this use.
- [Developer platform and data access](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data)
  distinguishes approved developer use, research and commercial contracts. Personal
  crypto investigation does not automatically qualify as academic research.
- [Reddit for Researchers](https://support.reddithelp.com/hc/en-us/articles/49381918834964-Reddit-for-Researchers-Program)
  is free for eligible approved academic projects. Its BigQuery Analytics Hub data
  has a six-month delay and monthly updates. It is unsuitable for live monitoring.
  Access is restricted and redistribution is prohibited. Its actual approved schema
  must be inspected before writing an importer; this project does not pretend an
  RFR export is the same as an API Listing or provide guessed BigQuery queries.
- [Data API wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
  documents registered OAuth, a descriptive User-Agent, and 100 queries/minute per
  client ID for eligible free access averaged over ten minutes. Clients must inspect
  `X-Ratelimit-Used`, `X-Ratelimit-Remaining` and `X-Ratelimit-Reset`. These are API
  terms, not an RSS allowance. The wiki requires removal of deleted content and
  author identifiers and recommends routinely expiring stored data within 48 hours.
- [API reference](https://www.reddit.com/dev/api/)
  documents Listing cursors and native fullname IDs. Recent listings are not a
  guaranteed full archive. This implementation accepts native Listing children for
  synthetic replay but has no OAuth implementation or credential storage.
- [Public content policy](https://support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy),
  [User Agreement](https://redditinc.com/policies/user-agreement), and
  [robots.txt](https://www.reddit.com/robots.txt) do not establish permission for an
  unknown research crawler. The general crawler rule is disallow. No direct live
  RSS or anonymous JSON probes were run during implementation.

API keys are not universally paid. Some approved API access and the eligible RFR
program are free. Approval and suitability are the constraints here; paying a
third-party scraper would not resolve those constraints.

## Existing repositories inspected

| Project | Approach and finding | Fit for this project |
| --- | --- | --- |
| [ninjackster/reddit-rss-mcp](https://github.com/ninjackster/reddit-rss-mcp), [source](https://github.com/ninjackster/reddit-rss-mcp/blob/main/server.js) | Atom browsing, search and flat comments without keys. Source uses a Safari User-Agent, regex XML parsing and excerpt truncation. | Demonstrates feed readers exist. Do not copy browser impersonation or treat availability as research permission. |
| [jorgen-k/reddit-mcp](https://github.com/jorgen-k/reddit-mcp) | Claims keyless RSS access for local MCP reading. | Useful comparison for limited feeds. It does not grant access rights or supply durable evidence recovery. |
| [gguyon0925/n8n-reddit-scraper](https://github.com/gguyon0925/n8n-reddit-scraper) | Repository advertising scraper workflows. | A scraper workflow is not evidence of approved access. No code or dependencies adopted. |
| [praw-dev/praw](https://github.com/praw-dev/praw) | Established Python wrapper for the authenticated Reddit API. | Useful if a future approved API route exists; it does not remove OAuth or approval requirements. |

No third-party repository was executed or installed. The implementation uses
Python's standard library, parses XML structurally, preserves the raw Atom entry,
keeps complete text within explicit size bounds, identifies itself honestly, and
does not switch transports on a block.

## Bounded initial roster

The public community descriptions were inspected, not ingested as research data.
Live status and approval for each community still require verification.

| Community | Why include it | Bias or control to preserve |
| --- | --- | --- |
| [r/CryptoCurrency](https://www.reddit.com/r/CryptoCurrency/) | Broad crypto news and discussion | Mixed assets, reposts and general discussion |
| [r/dogecoin](https://www.reddit.com/r/dogecoin/) | Established meme-coin community | Enthusiast selection and non-market memes |
| [r/solana](https://www.reddit.com/r/solana/) | Ecosystem discussion relevant to meme-token activity | Technical and support posts unrelated to individual tokens |
| [r/CryptoMoonShots](https://www.reddit.com/r/CryptoMoonShots/) | Low-cap and meme-token promotion | Promotional claims, spam and unverified identities |

This is a research sampling frame, not a ranking of quality or investment value.
Posts and comments are separate targets. No private, quarantined or restricted
community access is attempted. Unrelated and uncertain content remains available
for controls; subreddit membership alone never establishes relevance.

## What keyless RSS can and cannot supply

The adapter preserves native IDs when present, canonical links, raw Atom entries,
feed publication/update fields and actual local receipt times. Missing author and
subreddit IDs stay null. Missing publication time stays null even if `updated`
exists. Parent-comment IDs, full threads, quotes, scores, deleted-content refresh
and complete history cannot be assumed from RSS. No numeric-ID substitution or
identity inference is allowed. A post's presence does not verify a token or a trade.

No current primary document found grants this project an unrestricted keyless
research route. RSS support is therefore implemented but inactive. If Reddit
approves RSS for this exact scope, next verify sample feed fields, quotas,
continuation behavior and deletion refresh before starting an isolated process.
If only RFR is approved, inspect its permitted export schema and implement a
separate importer without pretending its delayed data is live.
