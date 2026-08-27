# Automated strategy system

Status: implemented as live automation on 2026-08-25. The agent selects an existing saved strategy and entry time. The existing strategy scheduler submits and monitors the orders at that time.

## 1. Confirmed product decisions

- Users create complete strategies in Strategy Builder and save them in the database.
- The saved strategy owns its legs, expiry policy, exit rules, take profit, and stop loss.
- The AI cannot invent or modify a strategy. It selects one saved strategy and chooses its live entry time.
- The AI may postpone its decision by scheduling another agent run.
- Lots are automatic and use the account's common capital policy.
- The default account policy allocates 50% of total USD balance to each strategy, allowing two simultaneous allocations.
- Every strategy has a 100% maximum configured loss relative to its risk basis.
- Short-premium strategies use a combined-credit stop.
- Long-premium strategies use a combined-debit stop.
- A take-profit condition and a mandatory time exit run in parallel. The first valid exit condition closes the complete strategy.
- Times display in Asia/Kolkata. Storage and internal comparisons use UTC.

## 2. Correct meaning of a 100% stop

The loss is 100%, not 200%. The 200% number refers only to the close cost of a short-credit strategy.

### Short-credit strategy

Suppose a short straddle collects 200 in combined premium.

```text
entry credit = 200
100% stop-loss amount = 200
stop close cost = entry credit + allowed loss
stop close cost = 200 + 200 = 400
```

When the strategy costs 400 to close, the loss is 200. That loss equals 100% of the 200 credit received.

The system should display both values with unambiguous labels:

```text
Entry credit              200
Maximum configured loss   200, equal to 100% of credit
Stop close cost           400, equal to 200% of credit
```

### Long-debit strategy

Suppose a long straddle costs 200.

```text
entry debit = 200
100% stop-loss amount = 200
stop liquidation value = entry debit - allowed loss
stop liquidation value = 200 - 200 = 0
```

The long strategy cannot lose more than the debit paid, before fees. A 100% debit stop therefore represents the full premium at risk. A take profit, invalidation exit, or mandatory time exit will normally close it earlier.

### Defined-risk credit strategy

Iron condors and iron butterflies collect a net credit but also own protective wings. Their effective loss limit is:

```text
effective loss limit = minimum of:
  configured combined-credit stop loss
  structural maximum loss from the wings
```

## 3. Expiry policy and holding period are separate

The expiry selector chooses the option contract. It does not decide how long the system holds the trade.

### Expiry choices

```text
same_day  = nearest live expiry on the current Asia/Kolkata date
next_day  = nearest live expiry after the current date
7_day     = listed expiry closest to entry date plus 7 calendar days
30_day    = listed expiry closest to entry date plus 30 calendar days
```

If the exact target date is not listed, choose the closest later listed expiry. Every leg in a multi-leg strategy must use the same resolved expiry.

### Holding choices

```text
intraday
  Close at the configured same-day exit time.

hold_to_expiry
  Close at expiry minus the configured safety buffer.
  Initial default buffer: 5 minutes.
```

A strategy using a 30-day option can still be intraday. In that case, the system trades the 30-day contract but closes it on the same day. A strategy held until a 30-day expiry is positional, not intraday.

For the existing same-day short straddle:

```text
expiry policy: same_day
holding mode: hold_to_expiry
exit buffer: 5 minutes
```

## 4. Capital allocation and automatic lots

Capital allocation is an account setting and applies to every strategy. Strategy Builder does not store a capital percentage. Slot reservation happens before sizing so simultaneous workers cannot exceed the concurrency implied by the policy.

```text
full_balance        = 100% of total USD balance, maximum 1 live allocation
half_balance        = 50%, maximum 2 live allocations
one_third_balance   = 33.33%, maximum 3 live allocations
one_quarter_balance = 25%, maximum 4 live allocations
fixed_amount        = user-entered USD budget; concurrency is calculated from total balance
```

Every account defaults to `half_balance`. The engine calculates the nominal budget from total USD wallet balance and caps it by currently available balance. This means the first strategy can use half of total capital and a second can use the remaining half. The sizing engine keeps the existing 2% execution buffer, rounds lots down, and applies an optional strategy-level maximum-lots cap.

The system reserves a concurrency slot before submitting any leg. Two workers must never reserve the same slot.

### Long-premium sizing

```text
cost per lot = executable buy asks - executable sell bids, including contract multipliers
lots by capital = floor(usable capital cap / cost per lot)
```

### Short-premium sizing

```text
loss at configured stop per lot = entry credit per lot × stop-loss percentage

lots = minimum of:
  floor(usable capital cap / loss at configured stop per lot)
  configured maximum lots
```

### Defined-risk sizing

```text
structural maximum loss per lot = wing width value - net credit

lots = minimum of:
  floor(usable capital cap / structural maximum loss per lot)
  configured maximum lots
```

The account budget also validates manual lots. Delta performs the final exchange-margin validation when orders are submitted. If one lot does not fit the account budget or Delta rejects the required margin, the strategy cannot activate.

## 5. Common strategy exit controller

Every active strategy has one strategy-level exit controller. It monitors all legs as one unit.

Possible exits are:

```text
take_profit
stop_loss
invalidation
expiry_buffer
fixed_time
emergency
manual
```

The first successful claim starts a complete square-off. Later triggers see that the exit has already been claimed and do nothing.

### Take profit for credit strategies

```text
profit = entry credit - current close cost
take profit triggers when profit >= entry credit × takeProfitPercent
```

Example with 50% take profit:

```text
entry credit = 200
target profit = 100
target close cost = 100
```

### Take profit for debit strategies

```text
profit = current liquidation value - entry debit
take profit triggers when profit >= entry debit × takeProfitPercent
```

Example with 50% take profit:

```text
entry debit = 200
target profit = 100
target liquidation value = 300
```

The initial default may be 50%, but Strategy Builder must store it as a user-controlled parameter. Historical results may support different values for different strategies.

## 6. Strategy explanations and builder definitions

`Premium buying` is a category, not one strategy. Long call, long put, long straddle, and long strangle are premium-buying strategies.

### 6.1 Long call

#### What it does

A long call buys one call option. It benefits when BTC rises enough and soon enough for the call value to overcome its premium and time decay.

#### Suitable market

- Strong bullish prediction.
- Upward momentum on the relevant timeframes.
- Expected upside is greater than the premium hurdle.
- Direction is clearer than volatility alone.

#### Builder definition

```text
name: Long call
category: premium_buying
market outlook: bullish
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: net_debit
stop loss: debit percentage, maximum 100%
take profit: debit-return percentage
allocation: account capital policy
lots: auto
square-off: complete

legs:
  1. Buy call
     Strike: ATM by default
     Order type: market
```

#### Profit and loss

```text
entry debit = call premium
maximum loss = entry debit
expiry breakeven = call strike + entry debit per underlying unit
```

#### Main weakness

BTC can move upward but still fail to cover the premium and time decay.

### 6.2 Long put

#### What it does

A long put buys one put option. It benefits when BTC falls enough and soon enough for the put value to overcome its premium and time decay.

#### Suitable market

- Strong bearish prediction.
- Downward momentum on the relevant timeframes.
- Expected downside is greater than the premium hurdle.
- Direction is clearer than volatility alone.

#### Builder definition

```text
name: Long put
category: premium_buying
market outlook: bearish
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: net_debit
stop loss: debit percentage, maximum 100%
take profit: debit-return percentage
allocation: account capital policy
lots: auto
square-off: complete

legs:
  1. Buy put
     Strike: ATM by default
     Order type: market
```

#### Profit and loss

```text
entry debit = put premium
maximum loss = entry debit
expiry breakeven = put strike - entry debit per underlying unit
```

### 6.3 Long straddle

#### What it does

A long straddle buys an ATM call and ATM put with the same strike and expiry. It benefits from a large move in either direction or a strong increase in implied volatility. It loses value when BTC remains quiet because both legs experience time decay.

#### Suitable market

- Large move expected.
- Direction is uncertain.
- Volatility expansion expected.
- Expected movement is greater than the combined premium hurdle.

#### Builder definition

```text
name: Long ATM straddle
category: premium_buying
market outlook: large_move_unknown_direction
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: net_debit
stop loss: combined debit percentage, maximum 100%
take profit: combined debit-return percentage
allocation: account capital policy
lots: auto and equal for both legs
square-off: complete

legs:
  1. Buy ATM call
  2. Buy ATM put

same expiry and order type for both legs
```

#### Profit and loss

```text
entry debit = call premium + put premium
maximum loss = entry debit
upper expiry breakeven = ATM strike + entry debit
lower expiry breakeven = ATM strike - entry debit
```

#### Main weakness

The strategy pays two premiums. A move can be directionally correct but still too small or too late.

### 6.4 Long strangle

#### What it does

A long strangle buys an OTM call and OTM put with the same expiry. It costs less than a long straddle but needs a larger move because the options begin away from the current price.

#### Suitable market

- Very large move expected.
- Direction is uncertain.
- Lower entry cost is preferred over the tighter straddle breakevens.
- Enough time remains for BTC to reach one of the OTM strikes.

#### Builder definition

```text
name: Long strangle
category: premium_buying
market outlook: very_large_move_unknown_direction
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: net_debit
stop loss: combined debit percentage, maximum 100%
take profit: combined debit-return percentage
allocation: account capital policy
lots: auto and equal for both legs
square-off: complete

legs:
  1. Buy OTM call at configured strike steps
  2. Buy OTM put at configured strike steps

same expiry for both legs
```

#### Profit and loss

```text
entry debit = call premium + put premium
maximum loss = entry debit
upper expiry breakeven = call strike + entry debit
lower expiry breakeven = put strike - entry debit
```

### 6.5 Short straddle

#### What it does

A short straddle sells an ATM call and ATM put with the same strike and expiry. It benefits when BTC remains close to the ATM strike, volatility stays quiet, and time decay reduces both premiums.

This is the strategy already tested in the project.

#### Suitable market

- Strong boring-day or sideways prediction.
- Low realized volatility and ATR.
- Low relative volume with stable liquidity.
- Normal or unimportant news.
- No expected breakout during the holding window.

#### Builder definition

```text
name: Same-day short ATM straddle
category: premium_selling
market outlook: tight_sideways
price source: cash
holding mode: hold_to_expiry
expiry policy: same_day
exit buffer: 5 minutes
risk basis: net_credit
combined stop loss: 100% of entry credit
take profit: percentage of entry credit
allocation: account capital policy
lots: auto and equal for both legs
square-off: complete

legs:
  1. Sell ATM call
  2. Sell ATM put

order type: market
same expiry
leg-level targets, stops, trailing, and re-entry: empty
```

#### Profit and loss

```text
entry credit = call premium + put premium
maximum possible profit = entry credit
upper expiry breakeven = ATM strike + entry credit
lower expiry breakeven = ATM strike - entry credit
100% stop-loss amount = entry credit
stop close cost = 2 × entry credit
```

#### Exit

The first of these closes both legs:

```text
take profit
100% combined-credit stop
5 minutes before expiry
emergency or manual exit
```

### 6.6 Short strangle

#### What it does

A short strangle sells an OTM call and OTM put. It gives BTC a wider range than a short straddle but collects less premium.

#### Suitable market

- Sideways market expected.
- Expected range is wider than the range suitable for an ATM straddle.
- Low or falling volatility.
- Low event risk.
- The forecast price range remains between the two short strikes.

#### Builder definition

```text
name: Short strangle
category: premium_selling
market outlook: wide_sideways
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: net_credit
combined stop loss: 100% of entry credit
take profit: percentage of entry credit
allocation: account capital policy
lots: auto and equal for both legs
square-off: complete

legs:
  1. Sell OTM call at configured strike steps
  2. Sell OTM put at configured strike steps

same expiry
```

#### Profit and loss

```text
entry credit = call premium + put premium
maximum possible profit = entry credit
upper expiry breakeven = call strike + entry credit
lower expiry breakeven = put strike - entry credit
100% stop close cost = 2 × entry credit
```

### 6.7 Iron condor

#### What it does

An iron condor sells an OTM put and OTM call, then buys a further OTM put and call as protective wings. It benefits when BTC remains between the short strikes. Its profit and maximum loss are structurally limited.

#### Suitable market

- Range-bound market expected.
- Forecast range is wider than a butterfly range.
- Defined loss is preferred over uncovered short options.
- Volatility is expected to stay stable or fall.

#### Builder definition

Use four ordered strikes:

```text
lowest strike     long put
lower middle      short put
upper middle      short call
highest strike    long call
```

```text
name: Iron condor
category: defined_risk_premium_selling
market outlook: wide_sideways
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: defined_max_loss
combined stop loss: 100% of net entry credit
take profit: percentage of net entry credit
allocation: account capital policy
lots: auto and equal for all legs
square-off: complete

legs:
  1. Buy OTM put at outer strike steps
  2. Sell OTM put at inner strike steps
  3. Sell OTM call at inner strike steps
  4. Buy OTM call at outer strike steps

outer strike steps must be greater than inner strike steps
same expiry for all legs
```

#### Profit and loss

```text
net credit = short premiums - long premiums
maximum profit = net credit
maximum structural loss = wing width value - net credit
lower expiry breakeven = short put strike - net credit
upper expiry breakeven = short call strike + net credit
```

### 6.8 Iron butterfly

#### What it does

An iron butterfly sells an ATM call and ATM put, then buys an OTM call and put as protective wings. It resembles a short straddle with capped structural loss. It collects more credit than an iron condor but needs BTC to remain closer to ATM for the best outcome.

#### Suitable market

- Very tight range or ATM pinning expected.
- Stronger sideways confidence than an iron condor requires.
- Defined loss is preferred over an uncovered short straddle.
- ATM premium is attractive.

#### Builder definition

```text
name: Iron butterfly
category: defined_risk_premium_selling
market outlook: tight_sideways
price source: cash
holding mode: user selected
expiry policy: user selected
risk basis: defined_max_loss
combined stop loss: 100% of net entry credit
take profit: percentage of net entry credit
allocation: account capital policy
lots: auto and equal for all legs
square-off: complete

legs:
  1. Sell ATM call
  2. Sell ATM put
  3. Buy OTM call at configured wing steps
  4. Buy OTM put at the same wing steps

same expiry for all legs
```

#### Profit and loss

```text
net credit = ATM premiums sold - wing premiums bought
maximum profit = net credit
maximum structural loss = wing width value - net credit
upper expiry breakeven = ATM strike + net credit
lower expiry breakeven = ATM strike - net credit
```

## 7. Strategy Builder contract

Strategy Builder should save a complete, versioned strategy definition. The AI sees the saved definition but cannot change it.

### Identity and availability

```text
strategy id
strategy version
name
description
enabled for AI selection
category
market outlook
```

### Contract selection

```text
underlying
price source
holding mode
expiry policy
exit minutes before expiry
fixed exit time when applicable
strike rule for every leg
same-expiry requirement
```

### Risk and profit

```text
risk basis: net_debit, net_credit, or defined_max_loss
stop-loss percentage
take-profit percentage
complete square-off
emergency exit enabled
re-entry disabled initially
```

### Position sizing

```text
lots mode: auto or manual
maximum lots, optional hard cap
equal-lots requirement for balanced multi-leg strategies
```

Capital percentage and fixed-amount controls live in the separate account-level Capital allocation section.

### Legs

Each saved leg contains:

```text
side
option type
strike mode
strike steps or exact strike
resolved expiry policy
order type
lots mode
leg role, such as short_call or protective_put
```

## 8. AI market packet

The AI receives analysis data and saved strategies. It does not receive authority to edit risk parameters or lots.

### BTC market context

```text
1-minute, 15-minute, and daily price charts
15-minute spot-volume chart
rolling realized-volatility chart
Binance Spot order-book depth chart
Delta BTCUSD open-interest chart
returns and market structure
VWAP and distance from VWAP
ATR and realized volatility
CVD and aggressive trade flow
bid, ask, spread, and order-book imbalance
market structure and sideways score
```

### News context

```text
current news summary
BTC directional assessment
volatility risk
scheduled event times
event importance
source quality
contradictions
analysis expiry
```

### Option context

```text
available expiry dates
available strikes
call and put premiums
implied volatility
delta, gamma, theta, and vega
option open interest
```

Slippage handling, order recovery, actual margin checks, fill reconciliation, and execution fallbacks remain deterministic execution responsibilities. They are not part of the AI decision.

### Read-only market tools

`get_btc_market_packet` returns compact data rather than raw candle arrays:

```text
last price, 24-hour change, high, low, bid, ask, and volume
ATR, historical volatility, VWAP, CVD, market structure, and sideways score
1-minute, 15-minute, and daily OHLC, return, range, and volume summaries
top-of-book price, cumulative depth, and imbalance
recent aggressive buy and sell flow
Delta BTCUSD mark, index, basis, OI, funding, and execution quotes
```

`get_delta_option_context` returns:

```text
every exact listed settlement time
ATM call and put for each expiry
expiry-level OI, volume, spot, and listed-contract count
five strikes nearest spot for the selected expiry
bid, ask, mark, IV, delta, gamma, theta, vega, OI, volume, and contract value
```

The complete candles and all Delta option rows stay in the database snapshot. They are not copied into model context.

### Chart storage

Each chart is uploaded to the private Supabase `automation-charts` bucket. The model and Automation UI receive time-limited signed URLs for the same stored PNG objects. Account balances are never added to model context.

## 9. AI tools

### 9.1 `show_available_strategy`

Purpose: return every enabled saved strategy owned by the authenticated user.

The response should contain:

```text
strategy id and version
name and explanation
market outlook
legs
expiry and holding policy
take profit and stop loss
current availability
reason unavailable, if any
```

The tool must not return secrets or raw exchange credentials.

### 9.2 `select_strategy_and_time`

Purpose: select one existing strategy and schedule it on the live strategy engine.

Input:

```text
saved strategy id
saved strategy version
activation time
proposal expiry
AI confidence
reasoning summary
signals supporting the choice
signals that would invalidate it
market snapshot id
news analysis id
```

Server behavior:

1. Confirm the strategy still exists and the version matches.
2. Confirm the strategy is enabled for AI selection.
3. Confirm a concurrent execution slot is potentially available.
4. Resolve the listed Delta expiry from the saved expiry policy.
5. Materialize entry and exit timestamps without changing the saved risk rules.
6. Create the same `scheduled` strategy record used by manual Strategy Builder scheduling.
7. Store the AI decision and evidence for audit.
8. Leave order submission to the existing strategy scheduler at the activation time.

### 9.3 `scheduled_next_agent_run`

Purpose: let the AI wait for more evidence instead of forcing a strategy choice.

Input:

```text
next run time
reason for waiting
signals to inspect on the next run
current market snapshot id
current news analysis id
```

Rules:

- The requested time must be in the future.
- Store UTC and display Asia/Kolkata.
- Enforce a minimum delay to prevent rapid loops.
- Deduplicate identical pending runs.
- Limit the number of agent-created runs per day.
- A scheduled run cannot bypass the normal session and account controls.

The more conventional tool name would be `schedule_next_agent_run`, but this draft retains the requested name until the API naming is finalized.

## 10. Agent run schedule

The agent runs from fixed session triggers and its own approved follow-up triggers.

### Proposed session triggers

```text
Asia review
  09:00 Asia/Tokyo
  05:30 Asia/Kolkata

London review
  08:00 Europe/London
  12:30 or 13:30 Asia/Kolkata depending on daylight saving time

New York review
  09:30 America/New_York
  19:00 or 20:00 Asia/Kolkata depending on daylight saving time
```

Do not hardcode one permanent IST time for London and New York. Their daylight-saving changes must be converted from the session's local timezone.

Optional fixed India-time reviews can also be configured:

```text
start of trading day
noon review
afternoon review
final same-day expiry review
```

### Run outcomes

Every run ends in exactly one state:

```text
strategy_selected
wait_and_run_again
no_trade_for_current_window
```

Silence, malformed output, or a tool failure must not create a trade.

## 11. Strategy activation flow

```text
User creates and saves complete strategies
                    |
                    v
Fixed session or scheduled agent run starts
                    |
                    v
AI receives BTC, news, options, and saved-strategy context
                    |
          +---------+---------+
          |                   |
          v                   v
Select strategy          Wait for evidence
and activation time      and schedule next run
          |
          v
Proposal saved with evidence and expiry
          |
          v
Activation time arrives
          |
          v
Latest conditions and saved version revalidated
          |
    +-----+-----+
    |           |
    v           v
Eligible      Not eligible
    |           |
    v           v
Reserve slot  Record no trade
Calculate lots
Resolve expiry and legs
Execute and monitor
```

## 12. Implemented live system

- Strategy Builder stores risk basis, holding mode, expiry policy, take profit, auto-lot intent, and the enabled-for-AI flag.
- Capital allocation is stored once per account and defaults to 50% per strategy.
- Saved strategies have a database version. Proposals bind to that version and fail if it changes.
- The database stores agent runs, market snapshots, proposals, and policy-derived execution allocations.
- The strategy-level controller monitors combined credit and combined debit take profit and stop loss.
- The eight approved strategies have validated constructors and are stored once as shared, read-only defaults. User-created strategies remain private account rows.
- The DeepSeek vision team receives BTC price, volume, volatility, order-book, and Delta open-interest charts, Delta option context, account context, and one news sub-agent.
- `select_strategy_and_time` writes a live scheduled strategy. The existing scheduler retains order and monitoring authority.
- Asia, London, and New York triggers use their local timezones, so daylight-saving changes convert correctly.

## 13. Resolved defaults

1. Every template starts with a configurable 50% take profit.
2. Long strategies start with a configurable 100% debit stop.
3. One agent run can select at most one strategy.
4. The default 50% account policy supports two reserved or active allocations. Other percentages derive their limit from the same rule.
5. Every hold-to-expiry strategy starts with a five-minute buffer.
6. Agent follow-ups require at least five minutes and are limited to 12 runs per day.
7. The first fixed reviews are Asia, London, and New York. No extra India-time review is enabled initially.
8. The AI chooses the strategy without user-configured signal thresholds.

## 14. Live execution responsibilities

1. Resolve listed expiries from the policy before creating the scheduled record.
2. Calculate or validate lots from the account capital budget, executable option prices, product margin data, and risk basis.
3. Atomically reserve and release a policy-derived capital allocation around execution.
4. Use the saved strategy version selected by the agent.
5. Keep partial-fill recovery, position reconciliation, stops, targets, and time exits inside the deterministic engine.

## References

- [Options Industry Council strategy library](https://www.optionseducation.org/strategies/all-strategies-en)
- [Long straddle explanation](https://www.optionseducation.org/strategies/all-strategies/long-straddle)
- [Short straddles and strangles](https://www.optionseducation.org/videolibrary/volatility-strategies-ii-short-straddles-and-stran)
- [Iron condor and iron butterfly overview](https://www.optionseducation.org/news/october-webinar-key-takeaways)
