"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, RefreshCw, WifiOff } from "lucide-react";
import {
  AnimatedNumber, SectionHeading, Shimmer, SwapText, useSlidingPill
} from "@/app/components/ui";
import {
  bookPrice, compact, currencyCompact, errorMessage, humanize, money, price, signedCurrencyCompact, signedMoney,
  signedPercent
} from "@/lib/format";

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

type OrderBookSnapshot = {
  lastUpdateId: number;
  eventTime: number;
  bids: [number, number][];
  asks: [number, number][];
};

type RecentTrade = {
  id: number;
  price: number;
  quantity: number;
  quoteQuantity: number;
  time: number;
  side: "buy" | "sell";
  buyerIsMaker: boolean;
};

type DeltaHistoryPoint = { time: number; open: number; high: number; low: number; close: number };
type OpenInterestPoint = DeltaHistoryPoint & { valueUsd?: number; valueBtc?: number };
type DeltaBookLevel = [price: number, sizeContracts: number, depthContracts: number, sizeBtc: number];
type DeltaTrade = {
  id: string;
  price: number;
  sizeContracts: number;
  sizeBtc: number;
  notionalUsd: number;
  side: "buy" | "sell";
  time: number;
};
type DeltaProduct = {
  productId: number;
  symbol: string;
  description: string;
  state: string;
  tradingStatus: string;
  contractType: string;
  underlyingAsset: string;
  quotingAsset: string;
  settlingAsset: string;
  indexSymbol: string;
  indexDescription: string;
  contractValueBtc: number;
  tickSize: number;
  defaultLeverage: number;
  positionSizeLimitContracts: number;
  initialMarginPercent: number;
  maintenanceMarginPercent: number;
  makerFeePercent: number;
  takerFeePercent: number;
  fundingIntervalHours: number;
  launchTime?: string | null;
};

type DeltaContext = {
  available: boolean;
  source?: string;
  symbol?: string;
  instrumentType?: string;
  tradingStatus?: string;
  lastPrice?: number;
  open24h?: number;
  high24h?: number;
  low24h?: number;
  lastPriceChange24hPercent?: number;
  markPrice?: number;
  markHigh24h?: number;
  markLow24h?: number;
  markChange24hPercent?: number;
  markBasisPercent?: number;
  indexPrice?: number;
  openInterestBtc?: number;
  openInterestContracts?: number;
  openInterestUsd?: number;
  openInterestChange6hUsd?: number;
  volume24hBtc?: number;
  volume24hContracts?: number;
  turnover24hUsd?: number;
  fundingRatePercent?: number;
  leverage?: number;
  tickSize?: number;
  contractValueBtc?: number;
  priceBandLower?: number;
  priceBandUpper?: number;
  bestBid?: number;
  bestAsk?: number;
  bidSizeContracts?: number;
  askSizeContracts?: number;
  receivedAt?: number;
  lastError?: string | null;
  openInterestHistory: OpenInterestPoint[];
  fundingHistory?: DeltaHistoryPoint[];
  markPriceHistory?: DeltaHistoryPoint[];
  orderBook?: { symbol: string; bids: DeltaBookLevel[]; asks: DeltaBookLevel[] };
  recentTrades?: DeltaTrade[];
  product?: DeltaProduct;
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
  orderBook: OrderBookSnapshot;
  recentTrades: RecentTrade[];
  deltaContext: DeltaContext;
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
  orderBook: OrderBookSnapshot;
  recentTrades: RecentTrade[];
  deltaContext: DeltaContext;
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
  const loadRequestRef = useRef(0);
  const { barRef: intervalBar, pill: intervalPill } = useSlidingPill(interval, '[aria-pressed="true"]');

  useEffect(() => { intervalRef.current = interval; }, [interval]);

  const load = useCallback(async (quiet = false) => {
    const requestId = ++loadRequestRef.current;
    const requestedInterval = interval;
    if (!quiet) setLoading(true);
    try {
      const origin = marketDataOrigin();
      const response = await fetch(`${origin}/api/market/btcusd?interval=${requestedInterval}&limit=240`, {
        cache: "no-store",
        signal: AbortSignal.timeout(8_000),
      });
      const payload = await response.json().catch(() => ({})) as MarketResponse;
      if (!response.ok || !payload.success) {
        throw new Error(payload.error?.message || `Market request failed (${response.status})`);
      }
      if (payload.interval !== requestedInterval) {
        throw new Error(`Market response returned ${payload.interval || "an unknown interval"} instead of ${requestedInterval}`);
      }
      if (requestId !== loadRequestRef.current) return;
      setData({ ...payload, candles: normalizeCandles(payload.candles, 240) });
      setUpdatedAt(new Date());
      setError("");
    } catch (nextError) {
      if (requestId !== loadRequestRef.current) return;
      setError(errorMessage(nextError, "BTCUSDT market data is temporarily unavailable."));
    } finally {
      if (!quiet && requestId === loadRequestRef.current) setLoading(false);
    }
  }, [interval]);

  useEffect(() => {
    setHovered(null);
    void load();
    return () => { loadRequestRef.current += 1; };
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
        setFeedError(errorMessage(nextError, "Could not connect to live market updates."));
        setFeedState("offline");
        return;
      }
      socket.onopen = () => { attempt = 0; setFeedError(""); };
      socket.onmessage = event => {
        try {
          const update = JSON.parse(event.data) as MarketUpdate;
          if (update.type !== "market_update") return;
          setFeedState(update.realtime.connected ? "live" : "reconnecting");
          setFeedError(update.realtime.lastError ? errorMessage(new Error(update.realtime.lastError), "Live market updates are reconnecting.") : "");
          setUpdatedAt(new Date(update.receivedAt));
          setData(current => {
            if (!current) return current;
            const liveCandle = update.candles[intervalRef.current];
            const candles = current.interval === intervalRef.current && liveCandle
              ? mergeCandle(current.candles, liveCandle, 240)
              : current.candles;
            return {
              ...current,
              symbol: update.symbol,
              source: update.source,
              ticker: { ...current.ticker, ...update.ticker },
              candles,
              realtime: update.realtime,
              analysis: update.analysis,
              orderBook: update.orderBook,
              recentTrades: update.recentTrades,
              deltaContext: update.deltaContext,
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

  const chart = useMemo(
    () => chartGeometry(data?.interval === interval ? data.candles : []),
    [data?.candles, data?.interval, interval],
  );
  const activeCandle = chart.candles[hovered ?? Math.max(0, chart.candles.length - 1)];
  const positive = (data?.ticker.priceChangePercent || 0) >= 0;

  return <div className="market-page">
    <SectionHeading
      eyebrow="Live market data"
      title="Market analysis"
      description="Binance Spot order flow and volatility alongside Delta BTCUSD perpetual-market data."
      actions={
        <button type="button" className="button secondary" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={loading ? "spin" : ""} aria-hidden="true" />Refresh
        </button>
      }
    />

    <section className="market-terminal">
      <header className="market-toolbar">
        <div className="market-symbol">
          <span className="market-coin">₿</span>
          <div><strong>BTCUSDT</strong><small>Bitcoin / Tether · Spot market</small></div>
          <span className="market-source">BINANCE</span>
        </div>
        {/* A small set of mutually exclusive options with a moving highlight,
            which is exactly what the sliding-pill recipe is for: the control
            shows the move between timeframes, not just the destination. */}
        <div className="market-intervals" aria-label="Chart interval" ref={intervalBar}>
          {intervalPill}
          {intervals.map(item => <button
            key={item.value}
            aria-pressed={interval === item.value}
            className={interval === item.value ? "active" : ""}
            onClick={() => {
              if (interval === item.value) return;
              setLoading(true);
              setIntervalValue(item.value);
            }}
          >{item.label}</button>)}
        </div>
        {/* The feed state changes in place, so a drop to reconnecting reads as
            one status changing rather than two unrelated words. */}
        <div className={`market-feed-state${feedState !== "live" ? " stale" : ""}`}>
          <i /><SwapText>{feedLabel(feedState)}</SwapText>
        </div>
      </header>

      {data && <div className="market-stats">
        {/* The last trade arrives over a websocket and can sit unchanged for
            seconds at a time, so each update re-enters with a blurred slide.
            Without it a price tick is indistinguishable from a static number. */}
        <div className="market-last-price"><small>BTCUSDT · SPOT</small><strong><AnimatedNumber value={money(data.ticker.lastPrice)} minReplayMs={700} /></strong><span className={positive ? "up" : "down"}>{positive ? "+" : ""}{data.ticker.priceChange.toLocaleString(undefined, { maximumFractionDigits: 1 })} ({positive ? "+" : ""}{data.ticker.priceChangePercent.toFixed(2)}%)</span></div>
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
        {loading && !chart.candles.length ? <ChartLoading /> : data && chart.candles.length ? <svg
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

      {data && <MarketDetails
        orderBook={data.orderBook}
        trades={data.recentTrades}
        delta={data.deltaContext}
        spotPrice={data.ticker.lastPrice}
      />}

      {data && <DeltaMarketSection delta={data.deltaContext} binanceSpotPrice={data.ticker.lastPrice} />}

      <footer className="market-footer">
        <span><i /> Binance Spot BTCUSDT analysis · Delta BTCUSD derivative context</span>
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
  return normalizeCandles([...candles, candle], limit);
}

function normalizeCandles(candles: Candle[], limit: number) {
  const byOpenTime = new Map<number, Candle>();
  for (const candle of candles) {
    if (Number.isFinite(candle.openTime)) byOpenTime.set(candle.openTime, candle);
  }
  return Array.from(byOpenTime.values())
    .sort((left, right) => left.openTime - right.openTime)
    .slice(-limit);
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
    <AnalysisMetric label={`Average true range · ${analysis.atr.period}`} value={money(analysis.atr.value)} note={`${analysis.atr.percent.toFixed(3)}% of price`} />
    <AnalysisMetric label="Historical volatility" value={`${analysis.historicalVolatility.annualizedPercent.toFixed(1)}%`} note={`${analysis.historicalVolatility.sampleSize} one-minute returns`} />
    <AnalysisMetric label="Rolling volume-weighted price" value={money(analysis.vwap)} note="Last 240 one-minute candles" />
    <AnalysisMetric label={`Cumulative volume delta · ${analysis.cvd.window}`} value={`${cvdPositive ? "+" : ""}${analysis.cvd.baseVolume.toFixed(3)} BTC`} note={cvdPositive ? "Net market buying" : "Net market selling"} tone={cvdPositive ? "up" : "down"} />
    <AnalysisMetric label="Book imbalance" value={`${imbalance >= 0 ? "+" : ""}${imbalance.toFixed(1)}%`} note={`${analysis.orderBook.spreadBps.toFixed(2)} bps spread`} tone={imbalance >= 0 ? "up" : "down"} />
    <AnalysisMetric label="Market structure" value={analysis.marketStructure.state} note={`${analysis.marketStructure.strength.toFixed(0)}% trend separation`} tone={analysis.marketStructure.state === "bullish" ? "up" : analysis.marketStructure.state === "bearish" ? "down" : undefined} />
    <AnalysisMetric label="Range-bound estimate" value={`${analysis.sidewaysProbability.toFixed(0)}%`} note="Based on efficiency, range, and price deviation" />
  </section>;
}

function AnalysisMetric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "up" | "down" }) {
  return <div className="analysis-metric"><small>{label}</small><strong className={tone}>{value}</strong><span>{note}</span></div>;
}

function MarketDetails({ orderBook, trades, delta, spotPrice }: {
  orderBook: OrderBookSnapshot;
  trades: RecentTrade[];
  delta: DeltaContext;
  spotPrice: number;
}) {
  const bids = cumulativeLevels(orderBook.bids);
  const asks = cumulativeLevels(orderBook.asks);
  const bidLiquidity = bids.at(-1)?.total || 0;
  const askLiquidity = asks.at(-1)?.total || 0;
  const totalLiquidity = bidLiquidity + askLiquidity;
  const bidShare = totalLiquidity ? bidLiquidity / totalLiquidity * 100 : 50;
  const bestBid = orderBook.bids[0]?.[0] || 0;
  const bestAsk = orderBook.asks[0]?.[0] || 0;
  const spread = bestBid && bestAsk ? bestAsk - bestBid : 0;
  const buyVolume = trades.filter(trade => trade.side === "buy").reduce((sum, trade) => sum + trade.quantity, 0);
  const sellVolume = trades.filter(trade => trade.side === "sell").reduce((sum, trade) => sum + trade.quantity, 0);
  const flowTotal = buyVolume + sellVolume;
  const buyShare = flowTotal ? buyVolume / flowTotal * 100 : 50;

  return <section className="market-detail-grid" aria-label="Liquidity, order book, trades, and open interest">
    <article className="market-detail-card liquidity-panel">
      <DetailHeader eyebrow="BINANCE SPOT" title="Liquidity and order book" meta="Top 15 levels · 100 ms updates" />
      <div className="depth-summary">
        <div><small>Best bid</small><strong className="up">{bookPrice(bestBid)}</strong></div>
        <div className="spread-stat"><small>Spread</small><strong>${spread.toFixed(2)}</strong></div>
        <div><small>Best ask</small><strong className="down">{bookPrice(bestAsk)}</strong></div>
      </div>
      <DepthChart bids={bids} asks={asks} midpoint={spotPrice} />
      <div className="liquidity-balance" aria-label={`${bidShare.toFixed(1)} percent bid liquidity`}>
        <span className="bid-balance" style={{ width: `${bidShare}%` }} />
        <span className="ask-balance" style={{ width: `${100 - bidShare}%` }} />
      </div>
      <div className="balance-labels"><span>Bid depth {compact(bidLiquidity)} BTC</span><span>Ask depth {compact(askLiquidity)} BTC</span></div>
      <OrderBookTable bids={bids.slice(0, 10)} asks={asks.slice(0, 10)} />
    </article>

    <article className="market-detail-card delta-panel">
      <DetailHeader eyebrow="DELTA EXCHANGE" title="BTCUSD perpetual market" meta="Updates every 5 seconds" />
      {delta.available ? <>
        <div className="oi-hero">
          <small>Open interest</small>
          <strong>{currencyCompact(delta.openInterestUsd || 0)}</strong>
          <span>{compact(delta.openInterestBtc || 0)} BTC outstanding</span>
        </div>
        <OiSparkline points={delta.openInterestHistory || []} markPrice={delta.markPrice || 0} />
        <div className="delta-metrics">
          <ContextMetric label="Mark price" value={money(delta.markPrice || 0)} />
          <ContextMetric label="Index price" value={money(delta.indexPrice || 0)} />
          <ContextMetric label="Funding rate" value={`${(delta.fundingRatePercent || 0).toFixed(4)}%`} />
          <ContextMetric label="24h turnover" value={currencyCompact(delta.turnover24hUsd || 0)} />
          <ContextMetric label="Delta bid" value={money(delta.bestBid || 0)} />
          <ContextMetric label="Delta ask" value={money(delta.bestAsk || 0)} />
        </div>
        <div className="basis-row"><span>Spot / mark basis</span><strong className={(delta.markPrice || 0) >= spotPrice ? "up" : "down"}>{signedMoney((delta.markPrice || 0) - spotPrice)}</strong></div>
      </> : <div className="detail-empty"><WifiOff /><p>{delta.lastError ? errorMessage(new Error(delta.lastError), "Delta market data is temporarily unavailable.") : "Waiting for Delta open-interest data"}</p></div>}
      <p className="source-note">OI, mark, funding, and Delta quotes belong to the Delta BTCUSD perpetual. They are not Binance Spot values.</p>
    </article>

    <article className="market-detail-card trades-panel">
      <DetailHeader eyebrow="BINANCE SPOT" title="Recent trade flow" meta="Market-buy and market-sell volume" />
      <div className="trade-flow-summary">
        <div><small>Aggressive buys</small><strong className="up">{buyVolume.toFixed(3)} BTC</strong></div>
        <div className="flow-bar"><span className="buy-flow" style={{ width: `${buyShare}%` }} /><span className="sell-flow" style={{ width: `${100 - buyShare}%` }} /></div>
        <div><small>Aggressive sells</small><strong className="down">{sellVolume.toFixed(3)} BTC</strong></div>
      </div>
      <div className="recent-trades-grid" role="table" aria-label="Latest Binance Spot trades">
        {trades.slice(0, 12).map(trade => <div className="trade-tile" role="row" key={`${trade.id}-${trade.time}`}>
          <span className={trade.side === "buy" ? "up" : "down"}>{price(trade.price)}</span>
          <strong>{trade.quantity.toFixed(4)} BTC</strong>
          <time>{new Date(trade.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
        </div>)}
      </div>
    </article>
  </section>;
}

function DeltaMarketSection({ delta, binanceSpotPrice }: { delta: DeltaContext; binanceSpotPrice: number }) {
  if (!delta.available) return <section className="delta-market-section delta-market-empty" aria-label="Delta BTCUSD market data">
    <WifiOff /><div><h2>Delta BTCUSD market data</h2><p>{delta.lastError ? errorMessage(new Error(delta.lastError), "Delta market data is temporarily unavailable.") : "Waiting for Delta market data."}</p></div>
  </section>;

  const ltpChange = delta.lastPriceChange24hPercent || 0;
  const oiChange = delta.openInterestChange6hUsd || 0;
  const basisToBinance = (delta.markPrice || 0) - binanceSpotPrice;
  const bandLow = delta.priceBandLower || 0;
  const bandHigh = delta.priceBandUpper || 0;
  const bandPosition = bandHigh > bandLow ? Math.max(0, Math.min(100, ((delta.lastPrice || 0) - bandLow) / (bandHigh - bandLow) * 100)) : 50;
  const bids = delta.orderBook?.bids || [];
  const asks = delta.orderBook?.asks || [];
  const trades = delta.recentTrades || [];

  return <section className="delta-market-section" aria-label="Complete Delta Exchange BTCUSD public market data">
    <header className="delta-section-header">
      <div><small>DELTA EXCHANGE</small><h2>BTCUSD perpetual market</h2><p>Live market data for the contract used by scheduled strategies.</p></div>
      <div className="delta-live-state"><i /><span>{delta.tradingStatus || "Live"}</span><time>{delta.receivedAt ? `Updated ${new Date(delta.receivedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "Connecting"}</time></div>
    </header>

    <div className="delta-tape">
      <DeltaMetric label="Last price" value={money(delta.lastPrice || 0)} note={`${signedPercent(ltpChange)} · 24h`} tone={ltpChange >= 0 ? "up" : "down"} />
      <DeltaMetric label="Mark price" value={money(delta.markPrice || 0)} note={`${signedPercent(delta.markChange24hPercent || 0)} · 24h`} />
      <DeltaMetric label="Index price" value={money(delta.indexPrice || 0)} note={delta.product?.indexSymbol || ".DEXBTUSD"} />
      <DeltaMetric label="Open interest" value={currencyCompact(delta.openInterestUsd || 0)} note={`${signedCurrencyCompact(oiChange)} · 6h`} tone={oiChange >= 0 ? "up" : "down"} />
      <DeltaMetric label="Funding rate" value={`${(delta.fundingRatePercent || 0).toFixed(4)}%`} note={`${delta.product?.fundingIntervalHours || 8}h interval`} tone={(delta.fundingRatePercent || 0) >= 0 ? "up" : "down"} />
      <DeltaMetric label="24h volume" value={`${compact(delta.volume24hBtc || 0)} BTC`} note={`${compact(delta.volume24hContracts || 0)} contracts`} />
      <DeltaMetric label="24h turnover" value={currencyCompact(delta.turnover24hUsd || 0)} note="USD notional" />
      <DeltaMetric label="Mark / Binance basis" value={signedMoney(basisToBinance)} note={`${signedPercent(delta.markBasisPercent || 0)} Delta mark/index`} tone={basisToBinance >= 0 ? "up" : "down"} />
    </div>

    <div className="delta-range-strip">
      <div><span>24h low</span><strong>{money(delta.low24h || 0)}</strong></div>
      <div className="delta-band-track" aria-label={`Last price is ${bandPosition.toFixed(1)} percent through the Delta price band`}>
        <i style={{ left: `${bandPosition}%` }} /><span>Allowed price band</span>
      </div>
      <div><span>24h high</span><strong>{money(delta.high24h || 0)}</strong></div>
      <div><span>Band floor</span><strong>{money(bandLow)}</strong></div>
      <div><span>Band ceiling</span><strong>{money(bandHigh)}</strong></div>
    </div>

    <div className="delta-history-grid">
      <DeltaSeriesChart title="Open interest · 48h" points={delta.openInterestHistory || []} format={value => `${compact(value)} BTC`} />
      <DeltaSeriesChart title="Funding rate · 48h" points={delta.fundingHistory || []} format={value => `${value.toFixed(4)}%`} zeroLine />
      <DeltaSeriesChart title="Mark price · 48h" points={delta.markPriceHistory || []} format={value => money(value)} />
    </div>

    <div className="delta-detail-grid">
      <article className="delta-data-panel delta-book-panel">
        <DetailHeader eyebrow="DELTA EXCHANGE" title="Delta order book" meta="Top 15 levels · 5s snapshot" />
        <div className="delta-quote-row"><span><small>Best bid</small><strong className="up">{bookPrice(delta.bestBid || 0)}</strong><em>{compact(delta.bidSizeContracts || 0)} contracts</em></span><b>{((delta.bestAsk || 0) - (delta.bestBid || 0)).toFixed(2)} spread</b><span><small>Best ask</small><strong className="down">{bookPrice(delta.bestAsk || 0)}</strong><em>{compact(delta.askSizeContracts || 0)} contracts</em></span></div>
        <DeltaOrderBook bids={bids} asks={asks} />
      </article>

      <article className="delta-data-panel">
        <DetailHeader eyebrow="DELTA EXCHANGE" title="Recent BTCUSD trades" meta="Market-buy and market-sell trades" />
        <div className="delta-trade-head"><span>Price</span><span>Side</span><span>Size</span><span>Notional</span><span>Time</span></div>
        <div className="delta-trade-list">
          {trades.slice(0, 14).map(trade => <div className="delta-trade-row" key={trade.id}>
            <strong className={trade.side === "buy" ? "up" : "down"}>{bookPrice(trade.price)}</strong><span className={trade.side}>{trade.side}</span><span>{trade.sizeBtc.toFixed(3)} BTC</span><span>{currencyCompact(trade.notionalUsd)}</span><time>{new Date(trade.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
          </div>)}
          {!trades.length && <div className="delta-list-empty">Waiting for Delta public trades…</div>}
        </div>
      </article>

      <article className="delta-data-panel delta-contract-panel">
        <DetailHeader eyebrow="CONTRACT" title="BTCUSD specifications" meta="Perpetual futures" />
        <div className="delta-contract-grid">
          <ContractFact label="Instrument" value={humanize(delta.product?.contractType || delta.instrumentType || "perpetual_futures")} />
          <ContractFact label="Contract value" value={`${delta.product?.contractValueBtc || delta.contractValueBtc || .001} BTC`} />
          <ContractFact label="Tick size" value={`$${delta.product?.tickSize || delta.tickSize || .5}`} />
          <ContractFact label="Default leverage" value={`${delta.product?.defaultLeverage || delta.leverage || 0}×`} />
          <ContractFact label="Underlying" value={delta.product?.underlyingAsset || "BTC"} />
          <ContractFact label="Quote / settle" value={`${delta.product?.quotingAsset || "USD"} / ${delta.product?.settlingAsset || "USD"}`} />
          <ContractFact label="Initial margin" value={`${delta.product?.initialMarginPercent || 0}%`} />
          <ContractFact label="Maintenance margin" value={`${delta.product?.maintenanceMarginPercent || 0}%`} />
          <ContractFact label="Maker fee" value={`${(delta.product?.makerFeePercent || 0).toFixed(3)}%`} />
          <ContractFact label="Taker fee" value={`${(delta.product?.takerFeePercent || 0).toFixed(3)}%`} />
          <ContractFact label="Position limit" value={`${compact(delta.product?.positionSizeLimitContracts || 0)} contracts`} />
          <ContractFact label="Index" value={delta.product?.indexSymbol || ".DEXBTUSD"} />
        </div>
        <p className="source-note">Delta values describe the BTCUSD perpetual used by this app for execution. Binance values above describe BTCUSDT Spot and remain an independent market reference.</p>
      </article>
    </div>
  </section>;
}

function DeltaMetric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "up" | "down" }) {
  return <div className="delta-metric"><small>{label}</small><strong className={tone}>{value}</strong><span>{note}</span></div>;
}

function DeltaSeriesChart({ title, points, format, zeroLine = false }: { title: string; points: DeltaHistoryPoint[]; format: (value: number) => string; zeroLine?: boolean }) {
  const values = points.map(point => point.close);
  if (values.length < 2) return <article className="delta-series-card"><header><small>{title}</small><strong>Collecting…</strong></header><div className="delta-series-empty">Historical series will appear after the Delta refresh.</div></article>;
  const min = Math.min(...values, ...(zeroLine ? [0] : []));
  const max = Math.max(...values, ...(zeroLine ? [0] : []));
  const range = Math.max(Math.abs(max) * .000001, max - min, 1e-9);
  const y = (value: number) => 108 - (value - min) / range * 88;
  const line = values.map((value, index) => `${12 + index / (values.length - 1) * 336},${y(value)}`).join(" ");
  const change = values.at(-1)! - values[0];
  return <article className="delta-series-card">
    <header><small>{title}</small><strong>{format(values.at(-1)!)}</strong><span className={change >= 0 ? "up" : "down"}>{change >= 0 ? "+" : ""}{format(change)}</span></header>
    <svg viewBox="0 0 360 124" preserveAspectRatio="none" role="img" aria-label={`${title} from ${format(values[0])} to ${format(values.at(-1)!)}`}>
      {zeroLine && min < 0 && max > 0 && <line className="delta-zero-line" x1="12" x2="348" y1={y(0)} y2={y(0)} />}
      <polyline points={line} />
      <circle cx="348" cy={y(values.at(-1)!)} r="3" />
    </svg>
    <footer><span>{new Date(points[0].time).toLocaleDateString([], { day: "2-digit", month: "short" })}</span><span>Now</span></footer>
  </article>;
}

function DeltaOrderBook({ bids, asks }: { bids: DeltaBookLevel[]; asks: DeltaBookLevel[] }) {
  const maxSize = Math.max(1, ...bids.map(level => level[1]), ...asks.map(level => level[1]));
  const side = (levels: DeltaBookLevel[], kind: "bid" | "ask") => <div className="delta-book-side">
    <div className="delta-book-head"><span>{kind} price</span><span>Contracts</span><span>BTC</span></div>
    {levels.slice(0, 10).map(level => <div className="delta-book-row" key={`${kind}-${level[0]}`}><i className={kind} style={{ width: `${level[1] / maxSize * 100}%` }} /><strong className={kind === "bid" ? "up" : "down"}>{bookPrice(level[0])}</strong><span>{compact(level[1])}</span><span>{level[3].toFixed(3)}</span></div>)}
  </div>;
  return <div className="delta-order-book">{side(bids, "bid")}{side(asks, "ask")}</div>;
}

function ContractFact({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><strong>{value}</strong></div>;
}

type CumulativeLevel = { price: number; quantity: number; total: number };

function cumulativeLevels(levels: [number, number][]): CumulativeLevel[] {
  let total = 0;
  return levels.map(([levelPrice, quantity]) => {
    total += quantity;
    return { price: levelPrice, quantity, total };
  });
}

function DepthChart({ bids, asks, midpoint }: { bids: CumulativeLevel[]; asks: CumulativeLevel[]; midpoint: number }) {
  const width = 640;
  const height = 176;
  const center = width / 2;
  const maxDepth = Math.max(1, bids.at(-1)?.total || 0, asks.at(-1)?.total || 0);
  const chartBottom = 142;
  const chartTop = 18;
  const y = (value: number) => chartBottom - value / maxDepth * (chartBottom - chartTop);
  const bidPoints = bids.map((level, index) => ({ x: center - 8 - index * ((center - 26) / Math.max(1, bids.length - 1)), y: y(level.total) })).reverse();
  const askPoints = asks.map((level, index) => ({ x: center + 8 + index * ((center - 26) / Math.max(1, asks.length - 1)), y: y(level.total) }));
  const bidLine = bidPoints.map(point => `${point.x},${point.y}`).join(" ");
  const askLine = askPoints.map(point => `${point.x},${point.y}`).join(" ");
  const bidArea = bidPoints.length ? `10,${chartBottom} ${bidLine} ${center - 8},${chartBottom}` : "";
  const askArea = askPoints.length ? `${center + 8},${chartBottom} ${askLine} ${width - 10},${chartBottom}` : "";
  return <svg className="depth-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Cumulative Binance Spot bid and ask depth">
    <line className="depth-midline" x1={center} x2={center} y1="8" y2={chartBottom} />
    <polygon className="bid-depth-area" points={bidArea} />
    <polygon className="ask-depth-area" points={askArea} />
    <polyline className="bid-depth-line" points={bidLine} />
    <polyline className="ask-depth-line" points={askLine} />
    <text x="12" y="168">BIDS</text><text className="depth-mid-label" x={center} y="168">{price(midpoint)}</text><text x={width - 12} y="168" textAnchor="end">ASKS</text>
  </svg>;
}

function OrderBookTable({ bids, asks }: { bids: CumulativeLevel[]; asks: CumulativeLevel[] }) {
  const maxQuantity = Math.max(1, ...bids.map(level => level.quantity), ...asks.map(level => level.quantity));
  return <div className="order-book-table">
    <div className="book-side">
      <div className="book-head"><span>Bid price</span><span>Size BTC</span><span>Total</span></div>
      {bids.map(level => <div className="book-row" key={`bid-${level.price}`}>
        <i className="bid-level" style={{ width: `${level.quantity / maxQuantity * 100}%` }} />
        <span className="up">{bookPrice(level.price)}</span><span>{level.quantity.toFixed(4)}</span><span>{level.total.toFixed(3)}</span>
      </div>)}
    </div>
    <div className="book-side">
      <div className="book-head"><span>Ask price</span><span>Size BTC</span><span>Total</span></div>
      {asks.map(level => <div className="book-row" key={`ask-${level.price}`}>
        <i className="ask-level" style={{ width: `${level.quantity / maxQuantity * 100}%` }} />
        <span className="down">{bookPrice(level.price)}</span><span>{level.quantity.toFixed(4)}</span><span>{level.total.toFixed(3)}</span>
      </div>)}
    </div>
  </div>;
}

function OiSparkline({ points, markPrice }: { points: OpenInterestPoint[]; markPrice: number }) {
  const values = points.map(point => point.valueUsd || point.close * markPrice).filter(value => value > 0);
  if (values.length < 2) return <div className="oi-chart-empty">Collecting OI history…</div>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const coordinates = values.map((value, index) => `${index / (values.length - 1) * 300},${72 - (value - min) / range * 58}`).join(" ");
  return <div className="oi-chart-wrap">
    <svg className="oi-chart" viewBox="0 0 300 84" preserveAspectRatio="none" role="img" aria-label="Recent Delta open interest history">
      <polyline points={coordinates} />
    </svg>
    <span>Session OI range {currencyCompact(min)} – {currencyCompact(max)}</span>
  </div>;
}

function DetailHeader({ eyebrow, title, meta }: { eyebrow: string; title: string; meta: string }) {
  return <header className="detail-header"><div><small>{eyebrow}</small><h3>{title}</h3></div><span>{meta}</span></header>;
}

function ContextMetric({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><strong>{value}</strong></div>;
}

function ChartLoading() {
  return <div className="chart-loading"><BarChart3 /><span><Shimmer>Loading BTCUSDT candles</Shimmer></span><i /></div>;
}

function ChartError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="chart-error">
    <WifiOff aria-hidden="true" />
    <h3>Market data unavailable</h3>
    <p>{message}</p>
    <button type="button" className="button secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />Try again</button>
  </div>;
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
