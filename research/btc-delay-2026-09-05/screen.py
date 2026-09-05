"""Public-data diagnostic only. No account access or order endpoints."""
from __future__ import annotations

import hashlib
import io
import json
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'WIFUSDT',
           '1000PEPEUSDT', '1000BONKUSDT', '1000SHIBUSDT', '1000FLOKIUSDT']
MONTHS = ['2026-06', '2026-07']


def get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'PublicMarketResearch/1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def download(task: tuple[str, str]) -> tuple[str, str, pd.DataFrame, dict]:
    symbol, month = task
    filename = f'{symbol}-1m-{month}.zip'
    url = f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{filename}'
    archive = get_bytes(url)
    expected = get_bytes(url + '.CHECKSUM').decode().split()[0]
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise ValueError(f'Checksum mismatch: {filename}')
    (ROOT / 'raw' / filename).write_bytes(archive)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        members = zipped.namelist()
        if len(members) != 1:
            raise ValueError(f'Unexpected archive members: {filename}')
        frame = pd.read_csv(zipped.open(members[0]))
    required = ['open_time', 'open', 'close', 'count']
    if not set(required).issubset(frame.columns):
        raise ValueError(f'Unexpected columns: {filename}: {list(frame.columns)}')
    frame.index = pd.to_datetime(frame.open_time, unit='ms', utc=True)
    expected_index = pd.date_range(month, periods=len(frame), freq='min', tz='UTC')
    if not frame.index.equals(expected_index) or frame.index.has_duplicates:
        raise ValueError(f'Noncontiguous timestamps: {filename}')
    if (frame[['open', 'close']] <= 0).any().any():
        raise ValueError(f'Nonpositive prices: {filename}')
    return symbol, month, frame, {'url': url, 'sha256': actual, 'rows': len(frame),
                                'first': str(frame.index[0]), 'last': str(frame.index[-1])}


def main() -> None:
    (ROOT / 'raw').mkdir(parents=True, exist_ok=True)
    loaded: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in SYMBOLS}
    manifest = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for symbol, month, frame, source in pool.map(download, [(s, m) for s in SYMBOLS for m in MONTHS]):
            loaded[symbol].append(frame)
            manifest.append(source)
            print(f'{symbol} {month}: {len(frame)} verified minutes', flush=True)
    frames = {s: pd.concat(parts).sort_index() for s, parts in loaded.items()}
    closes = pd.DataFrame({s: f.close for s, f in frames.items()})
    returns = np.log(closes).diff()
    train = returns.index < pd.Timestamp('2026-07-01', tz='UTC')
    cutoff = float(returns.loc[train, 'BTCUSDT'].abs().quantile(.99))
    test = returns.loc[~train].copy()
    btc = test.BTCUSDT
    events = []
    last = -10
    for i, value in enumerate(btc):
        if abs(value) >= cutoff and i > last + 7:
            events.append(i)
            last = i
    rows, event_rows = [], []
    for symbol in SYMBOLS[3:]:
        asset = test[symbol]
        # July lead correlation and OLS incremental BTC coefficient, controlling own, ETH and SOL returns.
        regression = pd.DataFrame({'y': asset.shift(-1), 'btc': btc, 'own': asset,
                                   'eth': test.ETHUSDT, 'sol': test.SOLUSDT}).dropna()
        x = np.column_stack([np.ones(len(regression)), regression[['btc', 'own', 'eth', 'sol']]])
        coef = np.linalg.lstsq(x, regression.y.to_numpy(), rcond=None)[0]
        record = {'symbol': symbol, 'july_minutes': len(test), 'same_minute_corr': btc.corr(asset),
                  'btc_leads_1m_corr': btc.corr(asset.shift(-1)),
                  'btc_leads_3m_corr': btc.corr(asset.shift(-3)),
                  'btc_leads_5m_corr': btc.corr(asset.shift(-5)),
                  'reverse_1m_corr': asset.corr(btc.shift(-1)),
                  'btc_coefficient_with_own_eth_sol': float(coef[1]),
                  'zero_trade_minutes': int((frames[symbol].loc[test.index, 'count'] == 0).sum())}
        for side_name, sign_filter in [('both', 0), ('bull', 1), ('bear', -1)]:
            for delay in [0, 1]:
                vals = []
                for i in events:
                    sign = float(np.sign(btc.iloc[i]))
                    if sign_filter and sign != sign_filter:
                        continue
                    entry_i, exit_i = i + 1 + delay, i + 6 + delay
                    if exit_i >= len(test):
                        continue
                    # Opens are descriptive proxies, never executable quote claims.
                    op = frames[symbol].loc[test.index, 'open']
                    gross = sign * (float(op.iloc[exit_i]) / float(op.iloc[entry_i]) - 1) * 10000
                    event_rows.append({'symbol': symbol, 'side_group': side_name, 'extra_delay_minutes': delay,
                                       'signal_utc': str(test.index[i] + pd.Timedelta(minutes=1)),
                                       'btc_signal_bps': float(btc.iloc[i] * 10000), 'gross_bps': gross})
                    vals.append(gross)
                prefix = f'{side_name}_delay{delay}'
                record[prefix + '_n'] = len(vals)
                record[prefix + '_gross_bps'] = float(np.mean(vals)) if vals else None
                record[prefix + '_net_fee11_8bps'] = float(np.mean(vals) - 11.8) if vals else None
        rows.append(record)
    result = pd.DataFrame(rows)
    events_frame = pd.DataFrame(event_rows)
    result.to_csv(ROOT / 'minute-screen.csv', index=False)
    events_frame.to_csv(ROOT / 'event-diagnostics.csv', index=False)
    metadata = {'access_utc': datetime.now(timezone.utc).isoformat(), 'train': 'June 2026',
                'diagnostic_forward_period': 'July 2026', 'btc_abs_1m_threshold_bps': cutoff * 10000,
                'holding_minutes': 5, 'cooldown_signal_minutes': 8,
                'qualification': 'Exploratory Binance USD-M minute bars; not Delta executable backtest; no inference adjusted for multiple testing',
                'archives': manifest}
    (ROOT / 'data-manifest.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    with pd.ExcelWriter(ROOT / 'research-data.xlsx', engine='xlsxwriter') as writer:
        result.to_excel(writer, sheet_name='Minute screen', index=False)
        events_frame.to_excel(writer, sheet_name='Events', index=False)
        pd.DataFrame(manifest).to_excel(writer, sheet_name='Sources', index=False)
        pd.DataFrame([{'key': k, 'value': str(v)} for k, v in metadata.items() if k != 'archives']).to_excel(writer, sheet_name='Method', index=False)
        for sheet in writer.sheets.values():
            sheet.freeze_panes(1, 1)
            sheet.set_column(0, 30, 19)
    print(json.dumps(metadata, indent=2)[:500])
    print(result.to_string(index=False))


if __name__ == '__main__':
    main()
