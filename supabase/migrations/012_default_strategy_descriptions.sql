-- Give both traders and the automation agent actionable selection guidance for
-- each shared strategy without changing its legs, schedule, or risk controls.
begin;

do $$
declare
  default_count integer;
begin
  select count(*) into default_count
  from public.saved_strategies
  where is_default = true
    and name in (
      'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
      'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
    );

  if default_count <> 8 then
    raise exception 'Expected all eight shared default strategies; found %', default_count;
  end if;
end;
$$;

update public.saved_strategies
set definition_json = jsonb_set(
  definition_json,
  '{description}',
  to_jsonb(case name
    when 'Long call' then 'Use for a strong, time-bound bullish BTC view backed by momentum or a positive catalyst. The expected upside should comfortably exceed the ATM call premium and intraday time decay. Avoid when direction is uncertain, price is range-bound, or implied volatility makes the call expensive.'
    when 'Long put' then 'Use for a strong, time-bound bearish BTC view backed by downside momentum or a negative catalyst. The expected fall should comfortably exceed the ATM put premium and intraday time decay. Avoid when direction is uncertain, price is range-bound, or implied volatility makes the put expensive.'
    when 'Long ATM straddle' then 'Use before an imminent catalyst or breakout when BTC should move sharply today but direction is unclear. The expected move must exceed the combined ATM call and put debit. Avoid quiet sessions or entry after implied volatility has already priced an extreme move.'
    when 'Long strangle' then 'Use when BTC may make an exceptionally large move within about seven days but direction is unclear. The OTM options cost less than a straddle, but BTC must travel farther to profit. Avoid modest-move setups, slow markets, or overpriced implied volatility because both legs lose value to time decay.'
    when 'Short ATM straddle' then 'Use only when BTC is likely to stay tightly pinned near the current price through today''s expiry, realized volatility is subdued, option premium is rich, and no major catalyst is due. Avoid trends, breakouts, news events, or rising volatility. Both short legs carry uncapped tail risk.'
    when 'Short strangle' then 'Use when BTC should remain inside a well-supported wider range through today''s expiry and implied volatility is rich relative to the expected move. It gives more room than a short straddle but collects less premium. Avoid catalysts, directional momentum, expanding volatility, or uncertain range boundaries; tail risk is uncapped.'
    when 'Iron condor' then 'Use for a range-bound BTC session when implied volatility is rich and defined risk is preferred. The two short OTM strikes should sit beyond the expected range, while wider long wings cap loss. It offers more room but less credit than an iron butterfly. Avoid strong trends, breakouts, or major catalysts.'
    when 'Iron butterfly' then 'Use when BTC is likely to finish very near the current ATM strike through today''s expiry, implied volatility is rich, and capped risk is required. The ATM shorts provide higher credit than an iron condor, but the profitable range is narrower; OTM wings cap loss. Avoid drift, breakouts, and event risk.'
  end),
  true
)
where is_default = true
  and name in (
    'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
    'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
  );

commit;
