"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Clock3, Layers, RefreshCw, Shield, TrendingUp, Wallet
} from "lucide-react";
import { requestJson } from "@/lib/api";
import {
  EM_DASH, errorMessage, formatClock, money, percent, quantity, relativeTime, titleCase, toNumber
} from "@/lib/format";
import type { AccountOverview, DeltaRecord, RiskStrategy } from "@/lib/app-types";
import {
  AnimatedNumber, ConfirmModal, EmptyState, IconSwap, Meter, Panel, PanelHeader, Revealed,
  SectionHeading, SpinningCounter, StatusChip, SwapText, TableSkeleton, TileSkeleton, TiltCard,
  Toggle, type NoticeHandler, type StatusTone
} from "@/app/components/ui";

const AUTO_REFRESH_MS = 30_000;

/** Reads the first present key from a passthrough Delta record. */
function read(record: DeltaRecord, ...keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function readNumber(record: DeltaRecord, ...keys: string[]) {
  return toNumber(read(record, ...keys));
}

function readText(record: DeltaRecord, ...keys: string[]) {
  const value = read(record, ...keys);
  return value === undefined ? null : String(value);
}

type Wallet = {
  asset: string;
  balance: number;
  available: number;
  blocked: number;
  positionMargin: number;
  orderMargin: number;
};

function walletRows(balances: DeltaRecord[]): Wallet[] {
  return balances
    .map(row => ({
      asset: readText(row, "asset_symbol", "symbol") ?? "—",
      balance: readNumber(row, "balance") ?? 0,
      available: readNumber(row, "available_balance") ?? 0,
      blocked: readNumber(row, "blocked_margin") ?? 0,
      positionMargin: readNumber(row, "position_margin", "cross_position_margin") ?? 0,
      orderMargin: readNumber(row, "order_margin", "cross_order_margin") ?? 0
    }))
    .filter(wallet => wallet.balance !== 0 || wallet.blocked !== 0 || wallet.available !== 0)
    .sort((left, right) => right.balance - left.balance);
}

export default function Dashboard({ onNotice }: { onNotice: NoticeHandler }) {
  const [data, setData] = useState<AccountOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [cancelTarget, setCancelTarget] = useState<{ id: string; productId: number; label: string } | null>(null);
  const [closeTarget, setCloseTarget] = useState<{ productId: number; symbol: string; size: string } | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setData(await requestJson<AccountOverview>("/api/account/overview"));
      setRefreshedAt(new Date());
    } catch (loadError) {
      onNotice({ tone: "error", text: errorMessage(loadError) });
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [onNotice]);

  useEffect(() => { void load(); }, [load]);

  // Balances and positions move without user action, so the view refreshes
  // itself on a conservative cadence that stays well inside Delta rate limits.
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => { void load(true); }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const wallets = useMemo(() => walletRows(data?.balances ?? []), [data]);
  const primary = wallets[0];
  const positions = useMemo(() => data?.positions ?? [], [data]);
  const orders = useMemo(() => data?.orders ?? [], [data]);

  const positionMargin = useMemo(
    () => wallets.reduce((total, wallet) => total + wallet.positionMargin, 0),
    [wallets]
  );
  const unfilled = useMemo(
    () => orders.reduce((total, order) => total + Math.abs(readNumber(order, "unfilled_size", "size") ?? 0), 0),
    [orders]
  );

  async function cancelOrder() {
    if (!cancelTarget) return;
    try {
      await requestJson(`/api/orders/${cancelTarget.id}`, {
        method: "DELETE",
        body: JSON.stringify({ productId: cancelTarget.productId, confirm: true })
      });
      onNotice({ tone: "ok", text: `Order ${cancelTarget.label} cancelled.` });
      setCancelTarget(null);
      void load(true);
    } catch (actionError) {
      onNotice({ tone: "error", text: errorMessage(actionError) });
    }
  }

  async function closePosition() {
    if (!closeTarget) return;
    try {
      await requestJson(`/api/positions/${closeTarget.productId}/close`, {
        method: "POST",
        body: JSON.stringify({ confirm: true })
      });
      onNotice({ tone: "ok", text: `${closeTarget.symbol} position closed and verified on Delta.` });
      setCloseTarget(null);
      void load(true);
    } catch (actionError) {
      onNotice({ tone: "error", text: errorMessage(actionError) });
    }
  }

  return (
    <div className="portfolio">
      <SectionHeading
        eyebrow="Delta account"
        title="Portfolio"
        description="Wallet balances, margin usage, live positions, and outstanding orders."
        actions={
          <>
            <span className="refresh-state">
              <SwapText>{refreshedAt ? `Updated ${formatClock(refreshedAt.getTime())}` : "Loading"}</SwapText>
            </span>
            <button type="button" className="button secondary" onClick={() => void load()} disabled={loading}>
              {/* Both glyphs share one slot, so the control keeps its width while
                  the request is in flight and the row cannot reflow. */}
              <IconSwap
                showB={loading}
                a={<RefreshCw />}
                b={<RefreshCw className="spin" />}
              />
              Refresh
            </button>
          </>
        }
      />

      {loading && !data ? (
        <>
          <TileSkeleton count={4} />
          <Panel><TableSkeleton label="portfolio data" /></Panel>
        </>
      ) : (
        /* The placeholder above unmounts, so the reveal is carried by the content
           that replaces it — same clock, blur and easing as the cross-fade. */
        <Revealed>
          <div className="tile-grid">
            <Tile
              icon={<Wallet />}
              label={primary ? `${primary.asset} wallet balance` : "Wallet balance"}
              value={primary ? quantity(primary.balance, 4) : EM_DASH}
              note={primary ? `${quantity(primary.available, 4)} available to trade` : "No funded asset returned"}
            />
            <Tile
              icon={<Shield />}
              label="Margin in use"
              value={primary ? quantity(primary.blocked, 4) : EM_DASH}
              note={primary && primary.balance > 0
                ? `${percent((primary.blocked / primary.balance) * 100, 1)} of ${primary.asset} balance`
                : "Includes positions and open orders"}
              meter={primary && primary.balance > 0
                ? { value: primary.blocked, max: primary.balance, tone: utilisationTone(primary.blocked / primary.balance) }
                : undefined}
            />
            <Tile
              roll
              icon={<TrendingUp />}
              label="Open positions"
              value={String(positions.length)}
              note={positionMargin > 0 ? `${quantity(positionMargin, 4)} margin committed` : "No margin committed"}
            />
            <Tile
              roll
              icon={<Clock3 />}
              label="Open orders"
              value={String(orders.length)}
              note={unfilled > 0 ? `${quantity(unfilled, 2)} contracts unfilled` : "Nothing awaiting fill"}
            />
          </div>

          {data && data.riskStrategies.length > 0 && <CombinedRiskPanel strategies={data.riskStrategies} />}

          <PositionsPanel
            positions={positions}
            onClose={row => {
              const productId = readNumber(row, "product_id");
              if (!productId) return;
              setCloseTarget({
                productId,
                symbol: readText(row, "product_symbol", "symbol") ?? String(productId),
                size: String(read(row, "size") ?? "")
              });
            }}
          />

          <OrdersPanel
            orders={orders}
            onCancel={row => {
              const id = readText(row, "id", "order_id");
              const productId = readNumber(row, "product_id");
              if (!id || !productId) return;
              setCancelTarget({ id, productId, label: readText(row, "product_symbol", "symbol") ?? id });
            }}
          />

          <WalletsPanel wallets={wallets} />

          <div className="portfolio-footer">
            <Toggle
              label="Auto refresh"
              description={`Reloads every ${AUTO_REFRESH_MS / 1000} seconds while this tab is open.`}
              checked={autoRefresh}
              onChange={setAutoRefresh}
            />
          </div>
        </Revealed>
      )}

      {cancelTarget && (
        <ConfirmModal
          title="Cancel open order?"
          description={`The ${cancelTarget.label} order will be cancelled on Delta Exchange. Quantities already filled cannot be reversed.`}
          confirm="Cancel order"
          cancel="Keep order"
          onClose={() => setCancelTarget(null)}
          onConfirm={() => void cancelOrder()}
        />
      )}

      {closeTarget && (
        <ConfirmModal
          title="Close entire position?"
          description={`${closeTarget.symbol} size ${closeTarget.size} will be closed with a reduce-only market order. Open orders for this contract are cancelled first, and the live position is verified afterwards.`}
          confirm="Close position"
          cancel="Keep position"
          onClose={() => setCloseTarget(null)}
          onConfirm={() => void closePosition()}
        />
      )}
    </div>
  );
}

function utilisationTone(ratio: number): StatusTone {
  if (ratio >= 0.8) return "negative";
  if (ratio >= 0.5) return "warning";
  return "active";
}

/**
 * Headline figure.
 *
 * The card leans toward the pointer with a soft glare. These are the one surface
 * in the app where that is appropriate — they are glanceable and carry no
 * controls, so nothing can move out from under a click — and the lean is kept
 * shallow so tabular figures stay easy to read.
 *
 * How the number arrives depends on what changing means:
 *
 *   - counts of live positions and orders roll like a reel, because going from
 *     two positions to three is a real event on the account;
 *   - balances and margin re-enter with a quiet blurred slide, because those
 *     drift on every thirty-second poll and a jackpot roll for a rounding change
 *     would be noise.
 */
function Tile({ icon, label, value, note, meter, roll = false }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  note: string;
  meter?: { value: number; max: number; tone: StatusTone };
  roll?: boolean;
}) {
  return (
    <TiltCard className="tile-tilt">
      <article className="tile">
        <span className="tile-icon" aria-hidden="true">{icon}</span>
        <p className="tile-label">{label}</p>
        <strong className="tile-value">
          {roll
            ? <SpinningCounter value={value} animateOnMount />
            : <AnimatedNumber value={value} />}
        </strong>
        {meter && <Meter value={meter.value} max={meter.max} tone={meter.tone} label={label} />}
        <p className="tile-note">{note}</p>
      </article>
    </TiltCard>
  );
}

/* ------------------------------------------------------------------ *
 * Positions
 * ------------------------------------------------------------------ */

function PositionsPanel({ positions, onClose }: { positions: DeltaRecord[]; onClose: (row: DeltaRecord) => void }) {
  return (
    <Panel>
      <PanelHeader
        icon={<TrendingUp />}
        title="Open positions"
        meta={`${positions.length} live ${positions.length === 1 ? "contract" : "contracts"} on Delta`}
      />
      {positions.length ? (
        <div className="table-scroll">
          <table className="data-table">
            <caption className="visually-hidden">Live Delta positions with entry, margin, and liquidation detail</caption>
            <thead>
              <tr>
                <th scope="col">Contract</th>
                <th scope="col">Side</th>
                <th scope="col" className="numeric">Size</th>
                <th scope="col" className="numeric">Entry</th>
                <th scope="col" className="numeric">Margin</th>
                <th scope="col" className="numeric">Liquidation</th>
                <th scope="col" className="numeric">Buffer from entry</th>
                <th scope="col" className="numeric">Realised P&amp;L</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((row, index) => {
                const symbol = readText(row, "product_symbol", "symbol") ?? `Product ${readNumber(row, "product_id") ?? index}`;
                const size = readNumber(row, "size") ?? 0;
                const entry = readNumber(row, "entry_price");
                const liquidation = readNumber(row, "liquidation_price");
                const margin = readNumber(row, "margin");
                const realised = readNumber(row, "realized_pnl");
                const long = size > 0;
                // Distance between entry and the liquidation trigger. Stated
                // against entry because the REST position payload carries no
                // mark price, and inventing one would misstate live risk.
                const buffer = entry && liquidation && entry > 0
                  ? (Math.abs(entry - liquidation) / entry) * 100
                  : null;
                return (
                  <tr key={`${symbol}-${index}`}>
                    <th scope="row">{symbol}</th>
                    <td><span className={`side-tag ${long ? "buy" : "sell"}`}>{long ? "Long" : "Short"}</span></td>
                    <td className="numeric">{quantity(Math.abs(size), 0)}</td>
                    <td className="numeric">{entry === null ? EM_DASH : money(entry)}</td>
                    <td className="numeric">{margin === null ? EM_DASH : quantity(margin, 4)}</td>
                    <td className="numeric">{liquidation === null ? EM_DASH : money(liquidation)}</td>
                    <td className="numeric">
                      {buffer === null
                        ? EM_DASH
                        : <span className={buffer < 5 ? "value negative" : buffer < 15 ? "value warning" : "value"}>{percent(buffer, 1)}</span>}
                    </td>
                    <td className="numeric">
                      {realised === null
                        ? EM_DASH
                        : <span className={realised >= 0 ? "value positive" : "value negative"}>{quantity(realised, 4)}</span>}
                    </td>
                    <td className="row-action">
                      <button type="button" className="button ghost small" onClick={() => onClose(row)}>Close</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          compact
          icon={<Layers />}
          title="No open positions"
          description="Positions opened by a scheduled strategy or manually on Delta appear here."
        />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 * Orders
 * ------------------------------------------------------------------ */

function OrdersPanel({ orders, onCancel }: { orders: DeltaRecord[]; onCancel: (row: DeltaRecord) => void }) {
  return (
    <Panel>
      <PanelHeader
        icon={<Clock3 />}
        title="Open orders"
        meta={`${orders.length} ${orders.length === 1 ? "order" : "orders"} awaiting fill or cancellation`}
      />
      {orders.length ? (
        <div className="table-scroll">
          <table className="data-table">
            <caption className="visually-hidden">Outstanding Delta orders</caption>
            <thead>
              <tr>
                <th scope="col">Contract</th>
                <th scope="col">Side</th>
                <th scope="col">Type</th>
                <th scope="col" className="numeric">Filled</th>
                <th scope="col" className="numeric">Price</th>
                <th scope="col">State</th>
                <th scope="col">Placed</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {orders.map((row, index) => {
                const symbol = readText(row, "product_symbol", "symbol") ?? `Product ${readNumber(row, "product_id") ?? index}`;
                const side = readText(row, "side") ?? "";
                const size = Math.abs(readNumber(row, "size") ?? 0);
                const unfilledSize = Math.abs(readNumber(row, "unfilled_size") ?? 0);
                const filled = Math.max(0, size - unfilledSize);
                const limitPrice = readNumber(row, "limit_price");
                const average = readNumber(row, "average_fill_price");
                const state = readText(row, "state") ?? "unknown";
                const created = readText(row, "created_at");
                return (
                  <tr key={readText(row, "id", "order_id") ?? index}>
                    <th scope="row">{symbol}</th>
                    <td><span className={`side-tag ${side === "buy" ? "buy" : "sell"}`}>{side === "buy" ? "Buy" : "Sell"}</span></td>
                    <td>{titleCase(readText(row, "order_type") ?? "—")}</td>
                    <td className="numeric">{quantity(filled, 0)} / {quantity(size, 0)}</td>
                    <td className="numeric">
                      {limitPrice !== null ? money(limitPrice) : average !== null ? money(average) : "Market"}
                    </td>
                    <td><StatusChip tone={orderTone(state)}>{titleCase(state)}</StatusChip></td>
                    <td>{created ? relativeTime(created) : EM_DASH}</td>
                    <td className="row-action">
                      <button type="button" className="button ghost small" onClick={() => onCancel(row)}>Cancel</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          compact
          icon={<Clock3 />}
          title="No open orders"
          description="Scheduled entries place their orders at the configured entry time."
        />
      )}
    </Panel>
  );
}

function orderTone(state: string): StatusTone {
  if (state === "open" || state === "pending") return "active";
  if (state === "closed" || state === "filled") return "positive";
  if (state === "cancelled") return "neutral";
  return "warning";
}

/* ------------------------------------------------------------------ *
 * Wallets
 * ------------------------------------------------------------------ */

/**
 * Per-asset table rather than a single summed figure: wallet assets are
 * denominated differently, so one total would be arithmetically meaningless.
 */
function WalletsPanel({ wallets }: { wallets: Wallet[] }) {
  return (
    <Panel>
      <PanelHeader
        icon={<Wallet />}
        title="Wallet balances"
        meta="Reported per asset; balances are not summed across denominations"
      />
      {wallets.length ? (
        <div className="table-scroll">
          <table className="data-table">
            <caption className="visually-hidden">Delta wallet balance by asset</caption>
            <thead>
              <tr>
                <th scope="col">Asset</th>
                <th scope="col" className="numeric">Balance</th>
                <th scope="col" className="numeric">Available</th>
                <th scope="col" className="numeric">Position margin</th>
                <th scope="col" className="numeric">Order margin</th>
                <th scope="col">Utilisation</th>
              </tr>
            </thead>
            <tbody>
              {wallets.map(wallet => (
                <tr key={wallet.asset}>
                  <th scope="row">{wallet.asset}</th>
                  <td className="numeric">{quantity(wallet.balance, 6)}</td>
                  <td className="numeric">{quantity(wallet.available, 6)}</td>
                  <td className="numeric">{quantity(wallet.positionMargin, 6)}</td>
                  <td className="numeric">{quantity(wallet.orderMargin, 6)}</td>
                  <td className="table-meter">
                    {wallet.balance > 0 ? (
                      <>
                        <Meter
                          value={wallet.blocked}
                          max={wallet.balance}
                          tone={utilisationTone(wallet.blocked / wallet.balance)}
                          label={`${wallet.asset} margin utilisation`}
                        />
                        <small>{percent((wallet.blocked / wallet.balance) * 100, 1)}</small>
                      </>
                    ) : EM_DASH}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState compact icon={<Wallet />} title="No funded assets" description="Delta returned no wallet with a balance." />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 * Combined-premium monitors
 * ------------------------------------------------------------------ */

/**
 * Live combined-premium usage. The bar is the fraction of the configured stop
 * that has been consumed, which is the number that decides whether the pair
 * closes, so it is shown as a proportion rather than a raw percentage.
 */
function CombinedRiskPanel({ strategies }: { strategies: RiskStrategy[] }) {
  // This panel only exists while a combined-premium run is being monitored, so
  // it slides into the portfolio region when monitoring starts instead of
  // appearing between frames and shunting the tables down.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <Panel className="t-panel-slide" data-open={open}>
      <PanelHeader
        icon={<Shield />}
        title="Combined premium protection"
        meta={`${strategies.length} monitored ${strategies.length === 1 ? "strategy" : "strategies"}`}
      />
      <ul className="risk-monitor-list">
        {strategies.map(strategy => {
          const state = strategy.riskState;
          const stop = toNumber(state.stopPercent) ?? 100;
          const used = Math.max(0, toNumber(state.progress) ?? 0);
          const status = String(state.status ?? strategy.status);
          const share = stop > 0 ? used / stop : 0;
          return (
            <li key={strategy.id}>
              <div className="risk-monitor-head">
                <strong>{strategy.name}</strong>
                <StatusChip tone={share >= 0.75 ? "negative" : share >= 0.4 ? "warning" : "active"}>{titleCase(status)}</StatusChip>
              </div>
              <Meter
                value={used}
                max={stop}
                tone={share >= 0.75 ? "negative" : share >= 0.4 ? "warning" : "active"}
                label={`${strategy.name} combined stop usage`}
              />
              <div className="risk-monitor-foot">
                <span><strong>{percent(used, 0)}</strong> of {percent(stop, 0)} stop</span>
                <span>{strategy.monitoredAt ? `Checked ${formatClock(strategy.monitoredAt)}` : "Awaiting first check"}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
