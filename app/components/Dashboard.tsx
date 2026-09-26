"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown, Clock3, Layers, RefreshCw, Shield, TrendingUp, Wallet
} from "@/app/components/icons";
import CapitalAllocation from "@/app/components/CapitalAllocation";
import { useCurrency } from "@/app/components/currency";
import { usePortfolioStream, type StreamFeed } from "@/app/components/usePortfolioStream";
import { requestJson } from "@/lib/api";
import {
  EM_DASH, errorMessage, formatClock, formatDateTime, percent, quantity, relativeTime, signedPercent, titleCase,
  toNumber
} from "@/lib/format";
import type { AccountOverview, DeltaRecord, RiskStrategy } from "@/lib/app-types";
import {
  describeOrder, isProtectiveOrder, positionViews, readNumber, readText, readValue, totalUnrealized,
  triggerMethodLabel, type PositionView, type ProtectiveOrder
} from "@/lib/portfolio";
import {
  AnimatedNumber, ConfirmModal, DetailList, EmptyState, IconSwap, Meter, Panel, PanelHeader, Revealed,
  SectionHeading, SpinningCounter, StatusChip, SwapText, TableSkeleton, TileSkeleton,
  Toggle, type NoticeHandler, type StatusTone
} from "@/app/components/ui";

const AUTO_REFRESH_MS = 30_000;
/** Live prices tick several times a second; a full digit replay at most this often. */
const PRICE_REPLAY_MS = 900;

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
      // Isolated and cross margin are reported separately and both are real
      // commitments; Delta sends "0" for the unused mode, so they are summed.
      positionMargin: (readNumber(row, "position_margin") ?? 0) + (readNumber(row, "cross_position_margin") ?? 0),
      orderMargin: (readNumber(row, "order_margin") ?? 0) + (readNumber(row, "cross_order_margin") ?? 0)
    }))
    .filter(wallet => wallet.balance !== 0 || wallet.blocked !== 0 || wallet.available !== 0)
    .sort((left, right) => right.balance - left.balance);
}

export default function Dashboard({ onNotice }: { onNotice: NoticeHandler }) {
  const { formatMoney, currencyCode } = useCurrency();
  const stream = usePortfolioStream(true);
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

  // The live stream is the source of truth while it is up. REST (which lags
  // Delta's socket by up to ten seconds) fills in whenever it is not.
  const live = stream.feed === "live" ? stream.account : null;
  const balances = live?.balances ?? data?.balances;
  const positionRecords = live?.positions ?? data?.positions;
  const orderRecords = live?.orders ?? data?.orders;
  const wallets = useMemo(() => walletRows(balances ?? []), [balances]);
  const primary = wallets[0];
  const orders = useMemo(() => orderRecords ?? [], [orderRecords]);
  const positions = useMemo(
    () => positionViews(positionRecords ?? [], orders, stream.prices),
    [positionRecords, orders, stream.prices]
  );
  const unrealized = useMemo(() => totalUnrealized(positions), [positions]);

  const positionMargin = useMemo(
    () => wallets.reduce((total, wallet) => total + wallet.positionMargin, 0),
    [wallets]
  );
  const protectiveCount = useMemo(() => orders.filter(isProtectiveOrder).length, [orders]);
  const unfilled = useMemo(
    () => orders
      .filter(order => !isProtectiveOrder(order))
      .reduce((total, order) => total + Math.abs(readNumber(order, "unfilled_size", "size") ?? 0), 0),
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
        title="Portfolio"
        actions={
          <>
            <FeedState feed={stream.feed} autoRefresh={autoRefresh} />
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

      {loading && !data && !live ? (
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
              label={primary ? primary.asset === "USD" && currencyCode === "INR" ? "USD wallet · INR value" : `${primary.asset} wallet balance` : "Wallet balance"}
              value={primary ? primary.asset === "USD" ? formatMoney(primary.balance, { digits: 4 }) : `${quantity(primary.balance, 4)} ${primary.asset}` : EM_DASH}
              note={primary ? `${primary.asset === "USD" ? formatMoney(primary.available, { digits: 4 }) : `${quantity(primary.available, 4)} ${primary.asset}`} available to trade` : "No funded asset found"}
            />
            <Tile
              icon={<Shield />}
              label="Margin in use"
              value={primary ? primary.asset === "USD" ? formatMoney(primary.blocked, { digits: 4 }) : `${quantity(primary.blocked, 4)} ${primary.asset}` : EM_DASH}
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
              note={[
                unrealized !== null ? `${formatMoney(unrealized, { signed: true })} unrealised` : null,
                positionMargin > 0
                  ? `${primary?.asset === "USD" ? formatMoney(positionMargin, { digits: 4 }) : quantity(positionMargin, 4)} margin committed`
                  : "No margin committed"
              ].filter(Boolean).join(" · ")}
            />
            <Tile
              roll
              icon={<Clock3 />}
              label="Open orders"
              value={String(orders.length)}
              note={[
                unfilled > 0 ? `${quantity(unfilled, 2)} contracts unfilled` : null,
                protectiveCount > 0 ? `${protectiveCount} protective ${protectiveCount === 1 ? "stop" : "stops"} armed` : null
              ].filter(Boolean).join(" · ") || "Nothing awaiting fill"}
            />
          </div>

          {/* The account-wide capital rule is derived from the balance above, so
              it is set here rather than on a route of its own. */}
          <CapitalAllocation onNotice={onNotice} />

          {data && data.riskStrategies.length > 0 && <CombinedRiskPanel strategies={data.riskStrategies} />}

          <PositionsPanel
            positions={positions}
            feed={stream.feed}
            onClose={view => {
              if (!view.productId) return;
              setCloseTarget({
                productId: view.productId,
                symbol: view.symbol,
                size: String(readValue(view.record, "size") ?? "")
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
  );
}

/** Header badge: where the figures on this screen are coming from right now. */
function FeedState({ feed, autoRefresh }: { feed: StreamFeed; autoRefresh: boolean }) {
  const polling = autoRefresh ? `Polling every ${AUTO_REFRESH_MS / 1000}s` : "Manual refresh";
  const label = feed === "live" ? "Live"
    : feed === "syncing" ? "Syncing with Delta"
      : feed === "unavailable" ? polling
        : feed === "connecting" ? "Connecting" : `Reconnecting · ${polling.toLowerCase()}`;
  return (
    <span className={feed === "live" ? "market-feed-state" : "market-feed-state stale"} role="status">
      <i aria-hidden="true" />
      <SwapText>{label}</SwapText>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * Positions
 * ------------------------------------------------------------------ */

const POSITION_COLUMNS = 8;

function PositionsPanel({ positions, feed, onClose }: {
  positions: PositionView[];
  feed: StreamFeed;
  onClose: (view: PositionView) => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = useCallback((key: string) => {
    setExpanded(current => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }, []);

  return (
    <Panel>
      <PanelHeader
        icon={<TrendingUp />}
        title="Open positions"
        meta={`${positions.length} live ${positions.length === 1 ? "contract" : "contracts"} on Delta${feed === "live" ? " · streaming" : ""}`}
      />
      {positions.length ? (
        <div className="table-scroll mobile-card-list">
          <table className="data-table mobile-card-table portfolio-card-table positions-table">
            <caption className="visually-hidden">
              Live Delta positions with notional, entry, mark and index prices, unrealised profit and loss, and protective orders
            </caption>
            <thead>
              <tr>
                <th scope="col">Contract</th>
                <th scope="col">Side</th>
                <th scope="col" className="numeric">Notional / Qty</th>
                <th scope="col" className="numeric">Entry</th>
                <th scope="col" className="numeric">Mark / Index</th>
                <th scope="col" className="numeric">Unrealised P&amp;L</th>
                <th scope="col">TP / SL</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            {positions.map(view => (
              <PositionRows
                key={view.key}
                view={view}
                open={expanded.has(view.key)}
                onToggle={toggle}
                onClose={onClose}
              />
            ))}
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

function pnlTone(value: number | null) {
  return value === null || value === 0 ? "value" : value > 0 ? "value positive" : "value negative";
}

/**
 * One position: the summary row and its disclosure row share a `<tbody>`, so
 * the pair stays together on the mobile card layout and for assistive tech.
 */
function PositionRows({ view, open, onToggle, onClose }: {
  view: PositionView;
  open: boolean;
  onToggle: (key: string) => void;
  onClose: (view: PositionView) => void;
}) {
  const { formatMoney } = useCurrency();
  const detailId = `position-detail-${view.key}`;
  const nearLiquidation = view.liquidationDistance !== null && view.liquidationDistance < 15;
  const lots = Math.abs(view.size);

  return (
    <tbody className="position-group t-acc" data-open={open}>
      <tr className="position-row">
        <th scope="row">
          <button
            type="button"
            className="position-toggle"
            aria-expanded={open}
            aria-controls={detailId}
            onClick={() => onToggle(view.key)}
          >
            <span className="t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
            <span className="cell-stack">
              <span>{view.symbol}</span>
              {nearLiquidation && view.liquidationDistance !== null && (
                <small className="value warning">Liquidation {percent(view.liquidationDistance, 1)} away</small>
              )}
            </span>
            <span className="visually-hidden">{open ? "Hide details" : "Show details"}</span>
          </button>
        </th>
        <td data-label="Side">
          <span className={`side-tag ${view.side === "long" ? "buy" : "sell"}`}>{view.side === "long" ? "Long" : "Short"}</span>
        </td>
        <td className="numeric" data-label="Notional / Qty">
          <span className="cell-stack">
            <span>{view.units !== null ? `${quantity(view.units, 4)} ${view.underlying ?? ""}`.trim() : EM_DASH}</span>
            <small>{view.size < 0 ? "-" : ""}{quantity(lots, 0)} {lots === 1 ? "lot" : "lots"}</small>
          </span>
        </td>
        <td className="numeric" data-label="Entry">{formatMoney(view.entry)}</td>
        <td className="numeric" data-label="Mark / Index">
          <span className="cell-stack">
            <span title={view.markSource === "snapshot" ? "Last account snapshot; live mark not received yet" : undefined}>
              {view.mark !== null ? <AnimatedNumber value={formatMoney(view.mark)} minReplayMs={PRICE_REPLAY_MS} /> : EM_DASH}
            </span>
            <small>{view.index !== null ? <AnimatedNumber value={formatMoney(view.index)} minReplayMs={PRICE_REPLAY_MS} /> : EM_DASH}</small>
          </span>
        </td>
        <td className="numeric" data-label="Unrealised P&L">
          {view.unrealizedPnl === null ? EM_DASH : (
            <span className={`cell-stack ${pnlTone(view.unrealizedPnl)}`}>
              <AnimatedNumber value={formatMoney(view.unrealizedPnl, { signed: true })} minReplayMs={PRICE_REPLAY_MS} />
              {view.unrealizedPercent !== null && <small>{signedPercent(view.unrealizedPercent, 2)}</small>}
            </span>
          )}
        </td>
        <td data-label="TP / SL">
          <span className="cell-stack protective-summary">
            <span><abbr title="Take profit">TP</abbr> {protectiveLabel(view.takeProfit, formatMoney)}</span>
            <span><abbr title="Stop loss">SL</abbr> {protectiveLabel(view.stopLoss, formatMoney)}</span>
          </span>
        </td>
        <td className="row-action" data-label="Position actions">
          <button type="button" className="button ghost small" onClick={() => onClose(view)}>Close</button>
        </td>
      </tr>
      <tr className="position-detail" id={detailId}>
        <td colSpan={POSITION_COLUMNS}>
          <div className="t-acc-panel" aria-hidden={!open}>
            <div className="t-acc-panel-inner" inert={open ? undefined : true}>
              <PositionDetail view={view} />
            </div>
          </div>
        </td>
      </tr>
    </tbody>
  );
}

type MoneyFormatter = ReturnType<typeof useCurrency>["formatMoney"];

function protectiveLabel(order: ProtectiveOrder | null, formatMoney: MoneyFormatter) {
  return order?.trigger != null ? formatMoney(order.trigger) : EM_DASH;
}

function protectiveItems(title: string, order: ProtectiveOrder | null, formatMoney: MoneyFormatter) {
  const method = triggerMethodLabel(order?.method ?? null);
  return [
    { label: `${title} trigger`, value: order?.trigger != null ? `${formatMoney(order.trigger)}${method ? ` on ${method}` : ""}` : EM_DASH },
    { label: `${title} limit`, value: order ? order.limit !== null ? formatMoney(order.limit) : "Market" : EM_DASH }
  ];
}

function PositionDetail({ view }: { view: PositionView }) {
  const { formatMoney } = useCurrency();
  const money = (value: number | null, digits = 2) => formatMoney(value, { digits });
  return (
    <div className="position-detail-grid">
      <section aria-label="Profit and loss">
        <h3>P&amp;L</h3>
        <DetailList items={[
          {
            label: "Unrealised",
            value: view.unrealizedPnl === null ? EM_DASH : (
              <span className={pnlTone(view.unrealizedPnl)}>
                {formatMoney(view.unrealizedPnl, { signed: true })}
                {view.unrealizedPercent !== null && ` (${signedPercent(view.unrealizedPercent, 2)})`}
              </span>
            )
          },
          {
            label: view.isOption ? (view.size < 0 ? "Premium received" : "Premium paid") : "Entry value",
            value: money(view.entryValue)
          },
          { label: "Realised cashflows", value: money(view.realizedCashflow) },
          {
            label: "Realised P&L",
            value: view.realizedPnl === null ? EM_DASH
              : <span className={pnlTone(view.realizedPnl)}>{formatMoney(view.realizedPnl, { signed: true })}</span>
          }
        ]} />
      </section>
      <section aria-label="Margin">
        <h3>Margin{view.marginMode ? <StatusChip tone="neutral">{titleCase(view.marginMode)}</StatusChip> : null}</h3>
        <DetailList items={[
          { label: "Assigned margin", value: money(view.margin, 4) },
          { label: "Effective leverage", value: view.effectiveLeverage !== null ? `${view.effectiveLeverage.toFixed(2)}x` : EM_DASH },
          {
            label: "Est. liquidation",
            value: view.liquidation === null ? "N.A." : (
              <>
                {money(view.liquidation)}
                {view.liquidationDistance !== null && (
                  <span className={view.liquidationDistance < 5 ? "value negative" : view.liquidationDistance < 15 ? "value warning" : "value"}>
                    {` · ${percent(view.liquidationDistance, 1)} from mark`}
                  </span>
                )}
              </>
            )
          },
          { label: "Closing fees reserved", value: money(view.commission, 4) }
        ]} />
      </section>
      <section aria-label="Take profit and stop loss">
        <h3>TP / SL</h3>
        <DetailList items={[
          ...protectiveItems("TP", view.takeProfit, formatMoney),
          ...protectiveItems("SL", view.stopLoss, formatMoney)
        ]} />
      </section>
      <section aria-label="Contract">
        <h3>Contract</h3>
        <DetailList items={[
          { label: "Type", value: view.contractType ? titleCase(view.contractType.replace(/_options$/, " option")) : EM_DASH },
          { label: "Strike", value: view.strike !== null ? money(view.strike) : EM_DASH },
          { label: "Expiry", value: view.settlementTime ? formatDateTime(view.settlementTime) : EM_DASH },
          { label: "Notional at index", value: money(view.notional) }
        ]} />
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Orders
 * ------------------------------------------------------------------ */

function OrdersPanel({ orders, onCancel }: { orders: DeltaRecord[]; onCancel: (row: DeltaRecord) => void }) {
  const { formatMoney } = useCurrency();
  return (
    <Panel>
      <PanelHeader
        icon={<Clock3 />}
        title="Open orders"
        meta={`${orders.length} ${orders.length === 1 ? "order" : "orders"} awaiting fill, trigger or cancellation`}
      />
      {orders.length ? (
        <div className="table-scroll mobile-card-list">
          <table className="data-table mobile-card-table portfolio-card-table">
            <caption className="visually-hidden">Outstanding Delta orders, including stop-loss and take-profit orders</caption>
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
                const order = describeOrder(row);
                const state = readText(row, "state") ?? "unknown";
                const created = readText(row, "created_at");
                // Delta's "pending" on a stop order means armed, not stuck.
                const stateLabel = order.protective && state === "pending" ? "Awaiting trigger" : titleCase(state);
                const method = triggerMethodLabel(order.triggerMethod);
                return (
                  <tr key={readText(row, "id", "order_id") ?? index}>
                    <th scope="row">{symbol}</th>
                    <td data-label="Side"><span className={`side-tag ${side === "buy" ? "buy" : "sell"}`}>{side === "buy" ? "Buy" : "Sell"}</span></td>
                    <td data-label="Type">{order.typeLabel}</td>
                    <td className="numeric" data-label="Filled">{quantity(filled, 0)} / {quantity(size, 0)}</td>
                    <td className="numeric" data-label="Price">
                      {order.protective && order.trigger !== null ? (
                        <span className="cell-stack">
                          <span>{formatMoney(order.trigger)}</span>
                          <small>Trigger{method ? ` on ${method}` : ""}{order.limit !== null ? ` · limit ${formatMoney(order.limit)}` : ""}</small>
                        </span>
                      ) : order.limit !== null ? formatMoney(order.limit) : order.average !== null ? formatMoney(order.average) : "Market"}
                    </td>
                    <td data-label="State"><StatusChip tone={orderTone(state)}>{stateLabel}</StatusChip></td>
                    <td data-label="Placed">{created ? relativeTime(created) : EM_DASH}</td>
                    <td className="row-action" data-label="Order actions">
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
  const { formatMoney, currencyCode } = useCurrency();
  const walletAmount = (wallet: Wallet, value: number) => wallet.asset === "USD"
    ? formatMoney(value, { digits: 6 }) : quantity(value, 6);
  return (
    <Panel>
      <PanelHeader
        icon={<Wallet />}
        title="Wallet balances"
        meta={`Reported per asset${currencyCode === "INR" ? "; USD values shown in INR" : ""}; balances are not summed`}
      />
      {wallets.length ? (
        <div className="table-scroll mobile-card-list">
          <table className="data-table mobile-card-table portfolio-card-table">
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
                  <td className="numeric" data-label="Balance">{walletAmount(wallet, wallet.balance)}</td>
                  <td className="numeric" data-label="Available">{walletAmount(wallet, wallet.available)}</td>
                  <td className="numeric" data-label="Position margin">{walletAmount(wallet, wallet.positionMargin)}</td>
                  <td className="numeric" data-label="Order margin">{walletAmount(wallet, wallet.orderMargin)}</td>
                  <td className="table-meter" data-label="Utilisation">
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
        <EmptyState compact icon={<Wallet />} title="No funded assets" description="Your Delta account has no wallet balance to display." />
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
