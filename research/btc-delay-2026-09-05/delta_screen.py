"""Sparse Delta candle diagnostic at preselected BTC shock times; read-only."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from screen import ROOT, get_bytes

SYMBOLS = ['DOGEUSD', '1000PEPEUSD', '1000SHIBUSD', 'WIFUSD', '1000BONKUSD', '1000FLOKIUSD']
START = int(pd.Timestamp('2026-07-01', tz='UTC').timestamp())
END = int(pd.Timestamp('2026-08-01', tz='UTC').timestamp())


def fetch_window(task: tuple[str, int]) -> tuple[str, dict, list[dict]]:
    symbol, start = task
    end = min(start + 1800 * 60, END)
    query = urlencode({'resolution': '1m', 'symbol': symbol, 'start': start, 'end': end - 1})
    url = 'https://api.india.delta.exchange/v2/history/candles?' + query
    payload = json.loads(get_bytes(url))
    if payload.get('success') is not True or not isinstance(payload.get('result'), list):
        raise ValueError(f'Bad response: {url}')
    rows = payload['result']
    if len(rows) >= 2000 or any(not start <= row['time'] < end for row in rows):
        raise ValueError(f'Unexpected window response: {url}')
    (ROOT / 'raw' / f'delta-{symbol}-{start}.json').write_text(json.dumps(payload), encoding='utf-8')
    return symbol, {'url': url, 'rows': len(rows)}, rows


def main() -> None:
    grouped: dict[str, list[dict]] = {s: [] for s in SYMBOLS}
    sources = []
    tasks = [(s, t) for s in SYMBOLS for t in range(START, END, 1800 * 60)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for index, (symbol, source, rows) in enumerate(pool.map(fetch_window, tasks)):
            grouped[symbol].extend(rows)
            sources.append(source)
            if (index + 1) % 25 == 0:
                print(f'{index + 1}/{len(tasks)} Delta candle windows retrieved', flush=True)
    triggers = pd.read_csv(ROOT / 'event-diagnostics.csv')
    triggers = triggers[(triggers.symbol == 'DOGEUSDT') & (triggers.side_group == 'both') & (triggers.extra_delay_minutes == 0)]
    summaries, trade_rows = [], []
    for symbol, rows in grouped.items():
        frame = pd.DataFrame(rows)
        if frame.empty:
            summaries.append({'symbol': symbol, 'observed_minutes': 0})
            continue
        frame.index = pd.to_datetime(frame.time, unit='s', utc=True)
        if frame.index.has_duplicates:
            raise ValueError(f'Duplicate Delta candles: {symbol}')
        frame = frame.sort_index()
        if (frame[['open', 'close']] <= 0).any().any():
            raise ValueError(f'Nonpositive Delta prices: {symbol}')
        row = {'symbol': symbol, 'observed_minutes': len(frame), 'expected_minutes': 44640,
               'coverage': len(frame) / 44640, 'july_first': str(frame.index.min()), 'july_last': str(frame.index.max())}
        for delay in [0, 1]:
            available = []
            for event in triggers.itertuples():
                entry = pd.Timestamp(event.signal_utc) + pd.Timedelta(minutes=delay)
                exit_time = entry + pd.Timedelta(minutes=5)
                full_window = pd.date_range(entry, exit_time, freq='min')
                # No interpolation or crossing candle gaps, even when both endpoints exist.
                if not full_window.isin(frame.index).all():
                    continue
                gross = float(np.sign(event.btc_signal_bps) * (frame.loc[exit_time, 'open'] / frame.loc[entry, 'open'] - 1) * 10000)
                available.append(gross)
                trade_rows.append({'symbol': symbol, 'signal_utc': event.signal_utc, 'delay_minutes': delay,
                                   'side': 'bull' if event.btc_signal_bps > 0 else 'bear', 'gross_bps': gross})
            row[f'delay{delay}_eligible_of_78'] = len(available)
            row[f'delay{delay}_gross_bps'] = float(np.mean(available)) if available else None
            row[f'delay{delay}_net_fee11_8bps'] = float(np.mean(available) - 11.8) if available else None
        summaries.append(row)
    summary = pd.DataFrame(summaries)
    summary.to_csv(ROOT / 'delta-minute-screen.csv', index=False)
    pd.DataFrame(trade_rows).to_csv(ROOT / 'delta-events.csv', index=False)
    (ROOT / 'delta-manifest.json').write_text(json.dumps({'access_utc': datetime.now(timezone.utc).isoformat(), 'sources': sources}, indent=2), encoding='utf-8')
    products = json.loads(get_bytes('https://api.india.delta.exchange/v2/products'))
    tickers = json.loads(get_bytes('https://api.india.delta.exchange/v2/tickers?contract_types=perpetual_futures'))
    snapshot = {'access_utc': datetime.now(timezone.utc).isoformat(),
                'products': [r for r in products['result'] if r['symbol'] in SYMBOLS],
                'tickers': [r for r in tickers['result'] if r['symbol'] in SYMBOLS]}
    (ROOT / 'delta-snapshot.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
    print(summary.to_string(index=False))


if __name__ == '__main__':
    main()
