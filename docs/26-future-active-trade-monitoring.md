# Future active-trade monitoring

This document records a later phase of work. The exit-choice and expiry-selection change does not implement these rules.

## Current behavior

The trading engine monitors active positions for strategy stop loss and take profit, reconciles fills and remaining exposure, and requests the scheduled exit. The activation recheck runs before entry. Agent analysis after entry does not currently manage the open trade against its original thesis.

## Proposed trade thesis

At selection, save a structured thesis alongside the human-readable report: market regime, price levels and source, timeframe, expected move, event window, contract strikes and expiry, reasons for the chosen holding period, and explicit invalidation conditions. Keep the original thesis immutable. A later review should compare fresh evidence with it, the actual entry fills, remaining positions, realized and unrealized P&L, executable closing quotes, costs, and time left.

Conditions must identify an observable price source, threshold, timeframe, persistence rule, and intended response. A sentence such as "close if momentum weakens" is too ambiguous for automatic execution. An unavailable or stale input must have a defined response; it must never be interpreted as confirmation that the thesis still holds.

## Two levels of review

The deterministic engine should continue enforcing the existing financial stop, profit target, scheduled exit and contract cutoff independently of AI availability. A future portfolio exposure limit could assess all open positions and overlapping BTC and ETH risk against the account policy. Price, volatility, event and liquidity conditions could request an earlier thesis review or, only when explicitly approved in the entry policy, a deterministic exit.

An AI review could run at a bounded interval and on material events. Give it one open trade and the saved thesis. Initial allowed decisions should be continue, close early, or shorten the remaining hold. Persist its evidence and the action actually committed. Deduplicate event triggers and impose a cooldown; a failed analysis must not delay existing hard exits.

## Extensions and changes to exposure

An extension would require fresh evidence for the added window, a fixed maximum lifetime and extension count, remaining risk within the original account budget, an eligible contract that remains live, and no exit already in progress. The original deadline, revised deadline, author, reason and market evidence must remain visible. An extension must not reset accumulated P&L or trade age. Rolling contracts, changing strikes or adding size is a new exposure decision with separate accounting and execution safeguards.

## Evidence needed before enabling actions

Replay the proposed rules against saved market packets, historical option marks and executable quotes where available. Compare the same entry decisions under the existing scheduled exit, shorter fixed holds, and early-review actions. Include fees, missed exits, orders rejected for liquidity, trades that recovered after a hypothetical early exit, and maximum drawdown. Start with observation-only reviews, verify alerts and accounting, then permit limited actions behind a per-account switch. Preserve exactly one execution authority.
