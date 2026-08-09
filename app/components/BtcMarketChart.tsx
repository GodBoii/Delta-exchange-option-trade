"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, RefreshCw, WifiOff } from "lucide-react";

type Candle = {
  openTime: number;
  closeTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  contractVolume: number;
  baseVolume: number;
  quoteVolume?: number;
  tradeCount: number;
};

type MarketTicker = {
  lastPrice: number;
  priceChange: number;
  priceChangePercent: number;
  openPrice: number;
  highPrice: number;
  lowPrice: number;
  weightedAveragePrice: number;
  contractVolume: number;
  baseVolume: number;
  quoteVolume?: number;
  tradeCount: number;
  closeTime: number;
  bestBid?: number;
  bestAsk?: number;
};

type RealtimeStatus = {
  connected: boolean;
  bookSynced: boolean;
  lastEventAt?: number | null;
  lastTradeAt?: number | null;
  eventAgeMs?: number | null;
  lastError?: string | null;
  reconnects: number;
};

type MarketAnalysis = {
  computedAt: number;
  interval: string;
  atr: { value: number; percent: number; period: number };
  historicalVolatility: { annualizedPercent: number; sampleSize: number };
  vwap: number;
  cvd: { baseVolume: number; window: string };
  orderBook: {
    bestBid: number;
    bestAsk: number;
    spread: number;
    spreadBps: number;
    bidDepth: number;
    askDepth: number;
    imbalance: number;
  };
  marketStructure: { state: string; strength: number; ema20: number; ema50: number };
  sidewaysProbability: number;
};

type MarketResponse = {
  success: boolean;
  symbol: string;
  displaySymbol?: string;
  exchangeSymbol: string;
  source: string;
  interval: string;
  ticker: MarketTicker;
  candles: Candle[];
  realtime: RealtimeStatus;
  analysis: MarketAnalysis;
  error?: { message?: string };
};

type MarketUpdate = {
  type: "market_update";
  sequence: number;
  receivedAt: number;
  symbol: string;
  source: string;
  ticker: Partial<MarketTicker>;
  candles: Record<string, Candle>;
  realtime: RealtimeStatus;
  analysis: MarketAnalysis;
};

type FeedState = "connecting" | "live" | "reconnecting" | "offline";

const intervals = [
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
  { value: "1d", label: "1D" },
] as const;

const VIEW_WIDTH = 1200;
const VIEW_HEIGHT = 560;
const LEFT = 18;
const RIGHT = 88;
const TOP = 28;
const PRICE_BOTTOM = 423;
const VOLUME_TOP = 452;
const BOTTOM = 530;

export default function BtcMarketChart() {
  const [interval, setIntervalValue] = useState("1h");
  const [data, setData] = useState<MarketResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [feedState, setFeedState] = useState<FeedState>("connecting");
  const [feedError, setFeedError] = useState("");
  const intervalRef = useRef(interval);

  useEffect(() => { intervalRef.current = interval; }, [interval]);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const origin = marketDataOrigin();
      const response = await fetch(`${origin}/api/market/btcusd?interval=${interval}&limit=240`, {
        cache: "no-store",
        signal: AbortSignal.timeout(8_000),
      });
      const payload = await response.json().catch(() => ({})) as MarketResponse;
      if (!response.ok || !payload.success) {
        throw new Error(payload.error?.message || `Market request failed (${response.status})`);
      }
      setData(payload);
      setUpdatedAt(new Date());
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "BTCUSDT market data is unavailable");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [interval]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let stopped = false;
    let attempt = 0;

    const connect = () => {
      if (stopped) return;
      setFeedState(attempt ? "reconnecting" : "connecting");
      try {
        socket = new WebSocket(`${marketDataWebSocketOrigin()}/ws/market/btcusd`);
      } catch (nextError) {
        setFeedError(nextError instanceof Error ? nextError.message : "Could not open the live market stream");
        setFeedState("offline");
        return;
      }
      socket.onopen = () => { attempt = 0; setFeedError(""); };
      socket.onmessage = event => {
        try {
          const update = JSON.parse(event.data) as MarketUpdate;
          if (update.type !== "market_update") return;
          setFeedState(update.realtime.connected ? "live" : "reconnecting");
          setFeedError(update.realtime.lastError || "");
          setUpdatedAt(new Date(update.receivedAt));
          setData(current => {
            if (!current) return current;
            const liveCandle = update.candles[intervalRef.current];
            return {
              ...current,
              symbol: update.symbol,
              source: update.source,
              ticker: { ...current.ticker, ...update.ticker },
              candles: liveCandle ? mergeCandle(current.candles, liveCandle, 240) : current.candles,
              realtime: update.realtime,
              analysis: update.analysis,
            };
          });
        } catch { setFeedError("The live feed returned an unreadable update"); }
      };
      socket.onerror = () => setFeedError("Live stream interrupted");
      socket.onclose = () => {
        if (stopped) return;
        attempt += 1;
        setFeedState("reconnecting");
        reconnectTimer = window.setTimeout(connect, Math.min(10_000, 500 * 2 ** Math.min(attempt, 5)));
      };
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  const chart = useMemo(() => chartGeometry(data?.candles || []), [data?.candles]);
  const activeCandle = chart.candles[hovered ?? Math.max(0, chart.candles.length - 1)];
  const positive = (data?.ticker.priceChangePercent || 0) >= 0;

  return <div className="market-page">
    <section className="page-heading market-heading" data-reveal>
      <div>
        <div className="eyebrow"><span /> Deep data analysis</div>
        <h1>Market analysis</h1>
        <p>Order flow, liquidity, volatility, and market structure in real time.</p>
      </div>
      <button className="secondary-button" onClick={() => void load()} disabled={loading}>
        <RefreshCw className={loading ? "spin" : ""} />Refresh
      </button>
    </section>

    <section className="market-terminal panel" data-reveal>
      <header className="market-toolbar">
        <div className="market-symbol">
          <span className="market-coin">₿</span>
          <div><strong>BTCUSDT</strong><small>Bitcoin / Tether · Spot analysis feed</small></div>
          <span className="market-source">BINANCE</span>
        </div>
        <div className="market-intervals" aria-label="Chart interval">
          {intervals.map(item => <button key={item.value} aria-pressed={interval === item.value} className={interval === item.value ? "active" : ""} onClick={() => setIntervalValue(item.value)}>{item.label}</button>)}
        </div>
        <div className={`market-feed-state${feedState !== "live" ? " stale" : ""}`}><i />{feedLabel(feedState)}</div>
      </header>

      {data && <div className="market-stats">
        <div className="market-last-price"><small>BTCUSDT · SPOT</small><strong>{money(data.ticker.lastPrice)}</strong><span className={positive ? "up" : "down"}>{positive ? "+" : ""}{data.ticker.priceChange.toLocaleString(undefined, { maximumFractionDigits: 1 })} ({positive ? "+" : ""}{data.ticker.priceChangePercent.toFixed(2)}%)</span></div>
        <MarketStat label="24h high" value={money(data.ticker.highPrice)} />
        <MarketStat label="24h low" value={money(data.ticker.lowPrice)} />
        <MarketStat label="24h volume" value={`${compact(data.ticker.baseVolume)} BTC`} />
        <MarketStat label="24h trades" value={compact(data.ticker.tradeCount)} />
      </div>}

      <div className="chart-readout" aria-live="polite">
        {activeCandle ? <>
          <span>{dateTime(activeCandle.openTime, interval)}</span>
          <span>O <b>{price(activeCandle.open)}</b></span>
          <span>H <b>{price(activeCandle.high)}</b></span>
          <span>L <b>{price(activeCandle.low)}</b></span>
          <span>C <b className={activeCandle.close >= activeCandle.open ? "up" : "down"}>{price(activeCandle.close)}</b></span>
          <span>Vol <b>{activeCandle.baseVolume.toFixed(2)} BTC</b></span>
          {data?.ticker.bestBid && <span>Bid <b>{price(data.ticker.bestBid)}</b></span>}
          {data?.ticker.bestAsk && <span>Ask <b>{price(data.ticker.bestAsk)}</b></span>}
        </> : <span>Waiting for candles…</span>}
      </div>

      <div className="chart-stage">
        {loading && !data ? <ChartLoading /> : data && chart.candles.length ? <svg
          className="candlestick-chart"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`BTCUSDT ${interval} candlestick chart with ${chart.candles.length} candles`}
          onPointerMove={event => {
            const rect = event.currentTarget.getBoundingClientRect();
            const x = ((event.clientX - rect.left) / rect.width) * VIEW_WIDTH;
            const index = Math.floor((x - LEFT) / chart.step);
            if (index >= 0 && index < chart.candles.length) setHovered(index);
          }}
          onPointerLeave={() => setHovered(null)}
        >
          <defs>
            <linearGradient id="volumeUp" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#55d7c4" stopOpacity=".52" /><stop offset="1" stopColor="#55d7c4" stopOpacity=".12" /></linearGradient>
            <linearGradient id="volumeDown" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ff766d" stopOpacity=".48" /><stop offset="1" stopColor="#ff766d" stopOpacity=".1" /></linearGradient>
          </defs>
          {chart.priceTicks.map(tick => <g key={tick.value} className="chart-grid"><line x1={LEFT} x2={VIEW_WIDTH - RIGHT} y1={tick.y} y2={tick.y} /><text x={VIEW_WIDTH - RIGHT + 12} y={tick.y + 4}>{price(tick.value)}</text></g>)}
          {chart.timeTicks.map(tick => <g key={`${tick.x}-${tick.label}`} className="chart-grid chart-time"><line x1={tick.x} x2={tick.x} y1={TOP} y2={BOTTOM} /><text x={tick.x} y={VIEW_HEIGHT - 10}>{tick.label}</text></g>)}
          <line className="volume-separator" x1={LEFT} x2={VIEW_WIDTH - RIGHT} y1={VOLUME_TOP - 14} y2={VOLUME_TOP - 14} />
          {chart.candles.map((candle, index) => {
            const x = LEFT + index * chart.step + chart.step / 2;
            const rising = candle.close >= candle.open;
            const openY = chart.y(candle.open);
            const closeY = chart.y(candle.close);
            const bodyY = Math.min(openY, closeY);
            const bodyHeight = Math.max(1.4, Math.abs(openY - closeY));
            const volumeY = chart.volumeY(candle.baseVolume);
            return <g key={candle.openTime} className={rising ? "candle up" : "candle down"}>
              <rect className="volume-bar" x={x - chart.bodyWidth / 2} y={volumeY} width={chart.bodyWidth} height={BOTTOM - volumeY} fill={rising ? "url(#volumeUp)" : "url(#volumeDown)"} />
              <line className="candle-wick" x1={x} x2={x} y1={chart.y(candle.high)} y2={chart.y(candle.low)} />
              <rect className="candle-body" x={x - chart.bodyWidth / 2} y={bodyY} width={chart.bodyWidth} height={bodyHeight} rx={0.6} />
            </g>;
          })}
          <line className="last-price-line" x1={LEFT} x2={VIEW_WIDTH - RIGHT} y1={chart.lastPriceY} y2={chart.lastPriceY} />
          <rect className="last-price-label" x={VIEW_WIDTH - RIGHT + 4} y={chart.lastPriceY - 11} width={78} height={22} rx={3} />
          <text className="last-price-text" x={VIEW_WIDTH - RIGHT + 43} y={chart.lastPriceY + 4}>{price(chart.lastClose)}</text>
          {hovered != null && activeCandle && <g className="chart-crosshair">
            <line x1={LEFT + hovered * chart.step + chart.step / 2} x2={LEFT + hovered * chart.step + chart.step / 2} y1={TOP} y2={BOTTOM} />
            <line x1={LEFT} x2={VIEW_WIDTH - RIGHT} y1={chart.y(activeCandle.close)} y2={chart.y(activeCandle.close)} />
            <circle cx={LEFT + hovered * chart.step + chart.step / 2} cy={chart.y(activeCandle.close)} r="4" />
          </g>}
        </svg> : <ChartError message={error || "No BTCUSDT candles were returned."} onRetry={() => void load()} />}
        {(error || feedError) && data && <div className="chart-stale-banner"><WifiOff />{error || feedError}. Reconnecting automatically.</div>}
      </div>

      {data?.analysis && <AnalysisGrid analysis={data.analysis} />}

      <footer className="market-footer">
        <span><i /> Source: Binance Spot · BTCUSDT · public WebSocket · analysis only</span>
        <span>{updatedAt ? `Updated ${updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "Connecting…"}</span>
      </footer>
    </section>
  </div>;
}

function marketDataOrigin() {
  const configured = (process.env.NEXT_PUBLIC_BINANCE_API_URL || "").trim().replace(/\/$/, "");
  if (configured) return configured;
  if (window.location.protocol === "https:") throw new Error("The Binance market-data service is not configured for this website");
  const host = ["localhost", "127.0.0.1"].includes(window.location.hostname) ? window.location.hostname : "localhost";
  return `http://${host}:8001`;
}

function marketDataWebSocketOrigin() {
  return marketDataOrigin().replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

function mergeCandle(candles: Candle[], candle: Candle, limit: number) {
  const next = candles.slice();
  if (next.length && next[next.length - 1].openTime === candle.openTime) next[next.length - 1] = candle;
  else next.push(candle);
  return next.slice(-limit);
}

function feedLabel(state: FeedState) {
  if (state === "live") return "Streaming";
  if (state === "reconnecting") return "Reconnecting";
  if (state === "offline") return "Offline";
  return "Connecting";
}

function chartGeometry(candles: Candle[]) {
  if (!candles.length) return emptyGeometry();
  const rawMin = Math.min(...candles.map(candle => candle.low));
  const rawMax = Math.max(...candles.map(candle => candle.high));
  const padding = Math.max((rawMax - rawMin) * .08, rawMax * .001);
  const min = rawMin - padding;
  const max = rawMax + padding;
  const range = Math.max(1, max - min);
  const maxVolume = Math.max(1, ...candles.map(candle => candle.baseVolume));
  const width = VIEW_WIDTH - LEFT - RIGHT;
  const step = width / candles.length;
  const y = (value: number) => TOP + ((max - value) / range) * (PRICE_BOTTOM - TOP);
  const volumeY = (value: number) => BOTTOM - (value / maxVolume) * (BOTTOM - VOLUME_TOP);
  const priceTicks = Array.from({ length: 6 }, (_, index) => {
    const value = max - (range * index / 5);
    return { value, y: y(value) };
  });
  const timeTickIndexes = Array.from(new Set(Array.from({ length: 6 }, (_, index) => Math.min(candles.length - 1, Math.round(index * (candles.length - 1) / 5)))));
  const timeTicks = timeTickIndexes.map(index => ({
    x: LEFT + index * step + step / 2,
    label: shortTime(candles[index].openTime),
  }));
  const lastClose = candles[candles.length - 1].close;
  return {
    candles,
    step,
    bodyWidth: Math.max(1.5, Math.min(7, step * .64)),
    y,
    volumeY,
    priceTicks,
    timeTicks,
    lastClose,
    lastPriceY: y(lastClose),
  };
}

function emptyGeometry() {
  return {
    candles: [] as Candle[], step: 1, bodyWidth: 1.5, y: () => 0, volumeY: () => BOTTOM,
    priceTicks: [] as { value: number; y: number }[], timeTicks: [] as { x: number; label: string }[],
    lastClose: 0, lastPriceY: 0,
  };
}

function MarketStat({ label, value }: { label: string; value: string }) {
  return <div className="market-stat"><small>{label}</small><strong>{value}</strong></div>;
}

function AnalysisGrid({ analysis }: { analysis: MarketAnalysis }) {
  const imbalance = analysis.orderBook.imbalance * 100;
  const cvdPositive = analysis.cvd.baseVolume >= 0;
  return <section className="analysis-grid" aria-label="Real-time spot analysis">
    <AnalysisMetric label={`ATR ${analysis.atr.period}`} value={money(analysis.atr.value)} note={`${analysis.atr.percent.toFixed(3)}% of price`} />
    <AnalysisMetric label="Historical volatility" value={`${analysis.historicalVolatility.annualizedPercent.toFixed(1)}%`} note={`${analysis.historicalVolatility.sampleSize} one-minute returns`} />
    <AnalysisMetric label="Rolling VWAP" value={money(analysis.vwap)} note="Last 240 one-minute candles" />
    <AnalysisMetric label={`CVD · ${analysis.cvd.window}`} value={`${cvdPositive ? "+" : ""}${analysis.cvd.baseVolume.toFixed(3)} BTC`} note={cvdPositive ? "Net aggressive buying" : "Net aggressive selling"} tone={cvdPositive ? "up" : "down"} />
    <AnalysisMetric label="Book imbalance" value={`${imbalance >= 0 ? "+" : ""}${imbalance.toFixed(1)}%`} note={`${analysis.orderBook.spreadBps.toFixed(2)} bps spread`} tone={imbalance >= 0 ? "up" : "down"} />
    <AnalysisMetric label="Market structure" value={analysis.marketStructure.state} note={`${analysis.marketStructure.strength.toFixed(0)}% trend separation`} tone={analysis.marketStructure.state === "bullish" ? "up" : analysis.marketStructure.state === "bearish" ? "down" : undefined} />
    <AnalysisMetric label="Sideways probability" value={`${analysis.sidewaysProbability.toFixed(0)}%`} note="Efficiency, range, and VWAP deviation" />
  </section>;
}

function AnalysisMetric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "up" | "down" }) {
  return <div className="analysis-metric"><small>{label}</small><strong className={tone}>{value}</strong><span>{note}</span></div>;
}

function ChartLoading() {
  return <div className="chart-loading"><BarChart3 /><span>Loading BTCUSDT candles…</span><i /></div>;
}

function ChartError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="chart-error"><WifiOff /><h3>Market feed unavailable</h3><p>{message}</p><button className="secondary-button" onClick={onRetry}><RefreshCw />Try again</button></div>;
}

function money(value: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 1 }).format(value);
}

function price(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

function compact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

function shortTime(value: number) {
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit" }).format(new Date(value));
}

function dateTime(value: number, interval: string) {
  const showTime = !["1d", "3d", "1w"].includes(interval);
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: "short", year: "2-digit",
    ...(showTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value));
}
