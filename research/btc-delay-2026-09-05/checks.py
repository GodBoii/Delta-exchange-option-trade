"""Independent arithmetic checks and uncertainty summaries for research artifacts."""
from __future__ import annotations

import json
from decimal import Decimal

import numpy as np
import pandas as pd

from screen import ROOT


def main() -> None:
    events = pd.read_csv(ROOT / 'delta-events.csv')
    events['day'] = pd.to_datetime(events.signal_utc, utc=True).dt.day
    snapshot = json.loads((ROOT / 'delta-snapshot.json').read_text())
    ticks = {t['symbol']: t for t in snapshot['tickers']}
    rng = np.random.default_rng(20260905)
    days = rng.integers(0, 31, size=(5000, 31))
    rows = []
    for symbol, all_coin in events.groupby('symbol'):
        group = all_coin[all_coin.delay_minutes == 0]
        daily = group.groupby('day').gross_bps.agg(['sum', 'count']).reindex(range(1, 32), fill_value=0)
        sums, counts = daily['sum'].to_numpy(), daily['count'].to_numpy()
        sampled_sums, sampled_counts = sums[days].sum(axis=1), counts[days].sum(axis=1)
        means = sampled_sums[sampled_counts > 0] / sampled_counts[sampled_counts > 0]
        tick = ticks[symbol]
        bid, ask = float(tick['quotes']['best_bid']), float(tick['quotes']['best_ask'])
        spread = (ask - bid) / ((ask + bid) / 2) * 10000
        row = {'symbol': symbol, 'events': len(group), 'gross_bps': group.gross_bps.mean(),
               'gross_ci95_low_unadjusted': np.quantile(means, .025),
               'gross_ci95_high_unadjusted': np.quantile(means, .975),
               'current_full_spread_bps': spread, 'current_turnover_usd': tick['turnover_usd'],
               'current_oi_usd': tick.get('oi_value_usd'),
               'illustrative_fee_plus_spread_net_bps': group.gross_bps.mean() - 11.8 - spread,
               'illustrative_optin_fee_plus_spread_net_bps': group.gross_bps.mean() - 5.9 - spread}
        rows.append(row)
        # Recompute one event directly from raw Delta JSON using Decimal arithmetic.
        sample = group.iloc[0]
        entry = int(pd.Timestamp(sample.signal_utc).timestamp())
        exit_time = entry + 300
        raw = {}
        for path in (ROOT / 'raw').glob(f'delta-{symbol}-*.json'):
            raw.update({r['time']: r for r in json.loads(path.read_text())['result']})
        direction = Decimal(1 if sample.side == 'bull' else -1)
        independent = direction * (Decimal(str(raw[exit_time]['open'])) / Decimal(str(raw[entry]['open'])) - 1) * 10000
        if abs(float(independent) - sample.gross_bps) > 1e-8:
            raise AssertionError(f'Event arithmetic mismatch: {symbol}')
    summary = pd.DataFrame(rows)
    summary.to_csv(ROOT / 'uncertainty-and-costs.csv', index=False)
    source_data = json.loads((ROOT / 'data-manifest.json').read_text())
    delta_sources = json.loads((ROOT / 'delta-manifest.json').read_text())
    sheets = {
        'Delta results': pd.read_csv(ROOT / 'delta-minute-screen.csv'),
        'Uncertainty and costs': summary,
        'Binance results': pd.read_csv(ROOT / 'minute-screen.csv'),
        'Delta events': events,
        'Binance events': pd.read_csv(ROOT / 'event-diagnostics.csv'),
        'Binance sources': pd.DataFrame(source_data['archives']),
        'Delta sources': pd.DataFrame(delta_sources['sources']),
        'Contract metadata': pd.DataFrame([{k: p.get(k) for k in ['symbol', 'id', 'contract_value', 'contract_unit_currency', 'tick_size', 'taker_commission_rate', 'maker_commission_rate']} for p in snapshot['products']]),
        'Method': pd.DataFrame([{'field': k, 'value': str(v)} for k, v in source_data.items() if k != 'archives'] + [
            {'field': 'CI', 'value': '5000 resamples of whole UTC days, 31 July days, seed 20260905; unadjusted for multiple testing; sparse n=71-78 events'},
            {'field': 'Spread stress', 'value': 'Current Sep 5 snapshot spread is illustrative only, NOT a historical measured spread or realized net backtest'},
            {'field': 'Order prices', 'value': 'Candle opens are last-trade proxies, NOT executable bid/ask or exact minute-boundary quotes'},
            {'field': 'Data coverage', 'value': 'No Delta forward fills. Six consecutive observed candle timestamps required for each five-minute event'},
            {'field': 'Costs', 'value': '11.8bps default / 5.9bps optional closing-fee waiver sensitivity; equal-notional approximation; excludes actual funding, impact, slippage and income taxes'},
            {'field': 'Raw data', 'value': 'Retained in raw/ with Binance published checksums and source manifests'}]),
    }
    with pd.ExcelWriter(ROOT / 'research-data.xlsx', engine='xlsxwriter') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes(1, 1)
            sheet.autofilter(0, 0, len(frame), len(frame.columns) - 1)
            sheet.set_column(0, len(frame.columns) - 1, 22)
    print(summary.to_string(index=False))
    print('Verified one raw-data event independently for every Delta coin; six checks passed.')


if __name__ == '__main__':
    main()
