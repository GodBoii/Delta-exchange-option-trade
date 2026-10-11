"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, Profile, RefreshCw, Save, Search } from "@/app/components/icons";
import { CAPITAL_MODE_OPTIONS, isCapitalAllocationMode } from "@/lib/capital";
import { useCurrency } from "@/app/components/currency";
import { requestJson } from "@/lib/api";
import { EM_DASH, errorMessage, formatDateTime, formatTimestamp, relativeTime, toNumber } from "@/lib/format";
import {
  allocationModeText, runStub, tradeQuery, walletUnavailableText
} from "@/lib/reporting";
import type {
  CapitalAllocationMode, OwnerTradeDetail, OwnerUserDetail, OwnerUserRow, OwnerUsersResponse, ReportRange, TradeItem
} from "@/lib/app-types";
import { RunDetailDialog } from "@/app/components/RunHistory";
import PnlCharts from "@/app/components/PnlCharts";
import PnlHeatmap from "@/app/components/PnlHeatmap";
import CapitalHistory from "@/app/components/OwnerCapitalHistory";
import { REPORT_ASSET_OPTIONS, pnlQuery, verifyReportAsset, type ReportAsset } from "@/lib/reporting";
import {
  PnlTiles, RangeControl, StateSelect, TradeTable, useTradePages, type StateFilter
} from "@/app/components/TradeReport";
import {
  DetailList, EmptyState, InlineMessage, NumberField, Panel, PanelHeader, SectionHeading, Select, StatusChip,
  StatusDot, TableSkeleton, TileSkeleton, Segmented, type NoticeHandler
} from "@/app/components/ui";

const PAGE_SIZE = 20;
const SEARCH_DELAY_MS = 300;
type DeletedFilter = "include" | "exclude" | "only";
const DELETED_OPTIONS: { value: DeletedFilter; label: string }[] = [
  { value: "include", label: "All runs" },
  { value: "exclude", label: "Still in user's history" },
  { value: "only", label: "Deleted by user" }
];

type Load<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: T };

function displayName(user: { displayName: string | null; email: string | null }) {
  return user.displayName || user.email?.split("@")[0] || "Unnamed user";
}

function connectionTone(status: string) {
  if (status === "connected") return "positive" as const;
  if (status === "not_connected") return "neutral" as const;
  return "warning" as const;
}

function connectionText(status: string) {
  if (status === "connected") return "Delta connected";
  if (status === "not_connected") return "Delta not connected";
  if (status === "revoked") return "Delta disconnected";
  return `Delta ${status.replaceAll("_", " ")}`;
}

/**
 * Owner Users section.
 *
 * Every registered account, the owner's own included, with its Delta connection,
 * capital and software trade results. The backend checks the owner role on every
 * request; this page is only a view of what it returns.
 */
export default function OwnerUsers({ onNotice }: { onNotice: NoticeHandler }) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [list, setList] = useState<Load<OwnerUsersResponse>>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [listToken, setListToken] = useState(0);
  const searchId = useId();
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(search.trim()); setOffset(0); }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let active = true;
    setList(previous => previous.kind === "ready" ? previous : { kind: "loading" });
    const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) });
    if (query) params.set("search", query);
    requestJson<OwnerUsersResponse>(`/api/owner/users?${params}`)
      .then(data => {
        if (!active) return;
        setList({ kind: "ready", data });
        setSelected(current => current ?? data.items[0]?.id ?? null);
      })
      .catch(error => { if (active) setList({ kind: "error", message: errorMessage(error) }); });
    return () => { active = false; };
  }, [listToken, offset, query]);

  function choose(id: string) {
    setSelected(id);
    // On a single column the detail sits below the list, so bring it into view.
    if (window.matchMedia("(max-width: 1080px)").matches) {
      const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth";
      window.requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: "start", behavior }));
    }
  }

  const data = list.kind === "ready" ? list.data : null;
  const lastPage = data ? offset + data.items.length >= data.matching : true;

  return (
    <div className="report-page owner-users">
      <SectionHeading
        title="Users"
        description="Every registered account, including yours. Figures cover runs this software placed; manual Delta trades are not included."
        actions={
          <button type="button" className="button secondary small" onClick={() => setListToken(token => token + 1)}>
            <RefreshCw aria-hidden="true" />Refresh
          </button>
        }
      />

      {data ? (
        <div className="owner-summary" aria-label="Account summary">
          <SummaryTile label="Registered users" value={data.summary.registeredUsers} />
          <SummaryTile label="Delta connected" value={data.summary.connectedAccounts} />
          <SummaryTile label="Automation on" value={data.summary.automationEnabledAccounts} />
          <SummaryTile
            label="Need attention"
            value={data.summary.accountsWithAttentionRuns + data.summary.automationWithoutConnection}
            detail={`${data.summary.accountsWithAttentionRuns} with stuck runs · ${data.summary.automationWithoutConnection} automation without Delta`}
            warn
          />
        </div>
      ) : list.kind === "loading" ? <TileSkeleton count={4} /> : null}

      <div className="owner-layout">
        <Panel className="owner-list-panel">
          <PanelHeader title="Accounts" meta={data ? `${data.matching} ${data.matching === 1 ? "account" : "accounts"}${query ? " match" : ""}` : undefined} />
          <div className="owner-search">
            <label htmlFor={searchId} className="visually-hidden">Search by name or email</label>
            <Search aria-hidden="true" />
            <input
              id={searchId}
              type="search"
              placeholder="Search name or email"
              autoComplete="off"
              value={search}
              maxLength={80}
              onChange={event => setSearch(event.target.value)}
            />
          </div>
          {list.kind === "error" ? (
            <div className="report-error">
              <InlineMessage tone="error">{list.message}</InlineMessage>
              <button type="button" className="button secondary small" onClick={() => setListToken(token => token + 1)}>
                <RefreshCw aria-hidden="true" />Try again
              </button>
            </div>
          ) : !data ? (
            <TableSkeleton label="accounts" rows={4} />
          ) : data.items.length ? (
            <ul className="owner-user-list" aria-label="Registered accounts">
              {data.items.map(user => (
                <li key={user.id}>
                  <UserRow user={user} selected={user.id === selected} onSelect={() => choose(user.id)} />
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState compact icon={<Profile />} title="No account matches" description="Try part of a name or email address." />
          )}
          {data && data.matching > PAGE_SIZE && (
            <div className="owner-pager">
              <button type="button" className="button ghost small" disabled={offset === 0}
                onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button>
              <span>{offset + 1}–{offset + data.items.length} of {data.matching}</span>
              <button type="button" className="button ghost small" disabled={lastPage}
                onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button>
            </div>
          )}
        </Panel>

        <div className="owner-detail" ref={detailRef}>
          {selected ? (
            <UserDetail
              key={selected}
              userId={selected}
              onNotice={onNotice}
              onAutomationSaved={() => setListToken(token => token + 1)}
              refreshToken={listToken}
            />
          ) : data ? (
            <Panel><EmptyState icon={<Profile />} title="Select an account" description="Choose an account to see its capital, automation and trades." /></Panel>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SummaryTile({ label, value, detail, warn = false }: { label: string; value: number; detail?: string; warn?: boolean }) {
  return (
    <div className={`detail-tile${warn && value > 0 ? " tone-negative" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small className="detail-tile-note">{detail}</small>}
    </div>
  );
}

function UserRow({ user, selected, onSelect }: { user: OwnerUserRow; selected: boolean; onSelect: () => void }) {
  const { currencyCode, formatMoneyNumber } = useCurrency();
  const net = toNumber(user.performance.netRealizedPnl);
  return (
    <button type="button" className="owner-user-row" aria-pressed={selected} onClick={event => {
      if (window.matchMedia("(max-width: 720px)").matches) {
        event.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
      }
      onSelect();
    }}>
      <span className="avatar" aria-hidden="true">{displayName(user).slice(0, 2).toUpperCase()}</span>
      <span className="owner-user-text">
        <strong>
          {displayName(user)}
          {user.isCurrentUser && <span className="owner-you">You</span>}
          {user.userType === "owner" && <span className="owner-role">Owner</span>}
        </strong>
        <small>{user.email ?? EM_DASH}</small>
        <span className="owner-user-meta">
          <StatusDot tone={connectionTone(user.account.connectionStatus)} />
          <span>{connectionText(user.account.connectionStatus)}</span>
          <span aria-hidden="true">·</span>
          <span>Automation {user.automationEnabled ? "on" : "off"}</span>
          {user.performance.attentionRuns > 0 && (
            <span className="owner-attention"><AlertTriangle aria-hidden="true" />{user.performance.attentionRuns} need attention</span>
          )}
        </span>
      </span>
      <span className={`owner-user-pnl${net ? (net > 0 ? " up" : " down") : ""}`}>
        {user.performance.settledRuns
          ? <>{formatMoneyNumber(user.performance.netRealizedPnl, { signed: true })}<small>{currencyCode}</small></>
          : <small>No settled runs</small>}
      </span>
    </button>
  );
}

function UserDetail({ userId, onNotice, onAutomationSaved, refreshToken }: {
  userId: string;
  onNotice: NoticeHandler;
  onAutomationSaved: () => void;
  refreshToken: number;
}) {
  const { currencyCode, formatMoneyNumber } = useCurrency();
  const [range, setRange] = useState<ReportRange>("all");
  const [asset, setAsset] = useState<ReportAsset>("all");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [deleted, setDeleted] = useState<DeletedFilter>("include");
  const [detail, setDetail] = useState<Load<OwnerUserDetail>>({ kind: "loading" });
  const [token, setToken] = useState(0);
  const [switching, setSwitching] = useState<"all" | "BTC" | "ETH" | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [inspecting, setInspecting] = useState<TradeItem | null>(null);
  const reportToken = token + refreshToken;
  const trades = useTradePages(
    `/api/owner/users/${encodeURIComponent(userId)}/trades?${tradeQuery({ range, asset, state: stateFilter, deleted })}`,
    asset, null, reportToken
  );
  const reportBase = `/api/owner/users/${encodeURIComponent(userId)}`;

  useEffect(() => {
    let active = true;
    setDetail(previous => previous.kind === "ready" ? previous : { kind: "loading" });
    requestJson<OwnerUserDetail>(`${reportBase}?${pnlQuery({ range, asset })}`, {
      signal: AbortSignal.timeout(20_000)
    })
      .then(data => { verifyReportAsset(data, asset); if (active) setDetail({ kind: "ready", data }); })
      .catch(error => { if (active) setDetail({ kind: "error", message: errorMessage(error) }); });
    return () => { active = false; };
  }, [range, asset, reportToken, reportBase]);

  const loadArchived = useCallback(async (): Promise<OwnerTradeDetail["run"]> => {
    if (!inspecting) throw new Error("No trade selected");
    const data = await requestJson<OwnerTradeDetail>(
      `/api/owner/users/${encodeURIComponent(userId)}/trades/${encodeURIComponent(inspecting.runId)}`
    );
    return data.run;
  }, [inspecting, userId]);

  async function setAutomation(enabled: boolean, target: "all" | "BTC" | "ETH") {
    if (detail.kind !== "ready" || switching !== null) return;
    setSwitching(target);
    setSwitchError(null);
    try {
      const saved = await requestJson<{ automation: OwnerUserDetail["automation"] }>(
        `${reportBase}/automation${target === "all" ? "" : `/assets/${target}`}`,
        { method: "PUT", body: JSON.stringify({ enabled }) }
      );
      // The switch shows the state the server saved, never the one that was requested.
      setDetail(current => current.kind === "ready"
        ? { kind: "ready", data: { ...current.data, automation: saved.automation } }
        : current);
      const savedEnabled = target === "all" ? saved.automation.enabled : saved.automation.assetEnabled[target];
      onNotice({
        tone: "ok",
        text: `${target === "all" ? "Account" : target} automation ${savedEnabled ? "enabled" : "paused"} for ${displayName(detail.data.profile)}.${savedEnabled ? "" : " Pending entries were cancelled. Open positions keep their exit rules."}`
      });
      onAutomationSaved();
    } catch (error) {
      setSwitchError(errorMessage(error));
    } finally {
      setSwitching(null);
    }
  }

  if (detail.kind === "loading") return <Panel><TableSkeleton label="account" rows={6} /></Panel>;
  if (detail.kind === "error") {
    return (
      <Panel>
        <OwnerReportControls asset={asset} range={range} onAssetChange={setAsset} onRangeChange={setRange} />
        <div className="report-error">
          <InlineMessage tone="error">{detail.message}</InlineMessage>
          <button type="button" className="button secondary small" onClick={() => setToken(value => value + 1)}>
            <RefreshCw aria-hidden="true" />Try again
          </button>
        </div>
      </Panel>
    );
  }

  const { profile, account, wallet, capitalPolicy, budgetPreview, capitalHistory, performance } = detail.data;
  const walletNote = walletUnavailableText(wallet);
  const userScope = performance.userScope;
  const scopeReady = (detail.data.asset ?? "all") === asset && detail.data.range === range;

  return (
    <>
      <Panel className="owner-profile-panel">
        <PanelHeader
          title={displayName(profile)}
          meta={profile.isCurrentUser ? "Your account" : profile.userType === "owner" ? "Owner" : "User"}
          actions={
            <button type="button" className="button ghost small" onClick={() => setToken(value => value + 1)}>
              <RefreshCw aria-hidden="true" />Reload
            </button>
          }
        />
        <DetailList items={[
          { label: "Email", value: profile.email ?? EM_DASH },
          { label: "Phone", value: profile.phoneNumber ?? EM_DASH },
          { label: "Registered", value: profile.registeredAt ? formatDateTime(profile.registeredAt) : EM_DASH },
          {
            label: "Delta",
            value: <StatusChip tone={connectionTone(account.connectionStatus)}>{connectionText(account.connectionStatus)}</StatusChip>
          },
          { label: "Delta account", value: account.accountName ? `${account.accountName}${account.email ? ` · ${account.email}` : ""}` : EM_DASH }
        ]} />
        <div className="owner-automation">
          <div className="owner-control-heading"><h3>Automation</h3><span>Account preferences</span></div>
          <div className="owner-automation-track" aria-label="Account automation controls" aria-busy={switching !== null}>
            {(["all", "BTC", "ETH"] as const).map(target => {
              const checked = target === "all" ? detail.data.automation.enabled : detail.data.automation.assetEnabled?.[target];
              const available = typeof checked === "boolean";
              const label = target === "all" ? "Account" : target;
              return <button key={target} type="button" role="switch" aria-checked={checked ?? false}
                aria-label={`${label} automation`} className={`owner-automation-switch${target === "all" ? " is-master" : ""}`}
                disabled={switching !== null || !available || (target !== "all" && !account.initialized)}
                onClick={() => void setAutomation(!checked, target)}>
                <span className="owner-asset-symbol" aria-hidden="true">{target === "BTC" ? "₿" : target === "ETH" ? "Ξ" : "◎"}</span>
                <span className="owner-switch-copy"><strong>{label}</strong>
                  <small>{switching === target ? "Saving…" : !available ? "Update API" : !checked ? "Paused"
                    : target !== "all" && !detail.data.automation.enabled ? "Standby" : "Enabled"}</small></span>
                <span className="owner-switch-knob" aria-hidden="true" />
              </button>;
            })}
          </div>
          <p className="field-hint">Pausing cancels pending automated entries. Open positions keep their exit rules. Global asset pauses still apply.</p>
          {!account.initialized || account.connectionStatus !== "connected" ? (
            <small className="field-hint">This account cannot trade until Delta is connected, even with automation on.</small>
          ) : null}
          {switchError && <InlineMessage tone="error">Automation was not changed. {switchError}</InlineMessage>}
        </div>
      </Panel>

      <details className="owner-capital-disclosure">
        <summary><span>Capital &amp; audit trail<small>{wallet.state === "live"
          ? `${formatMoneyNumber(wallet.totalBalance)} ${currencyCode} in wallet · ${formatMoneyNumber(wallet.availableBalance)} available`
          : "Wallet, strategy budget and recorded observations"}</small></span>
          <span className="owner-disclosure-action">Inspect capital <span aria-hidden="true">↗</span></span></summary>
      <Panel className="owner-capital-panel">
        <PanelHeader title="Capital" meta="Balances in USD on Delta, shown in your display currency" />
        <div className="detail-tiles">
          <div className="detail-tile">
            <span>Wallet balance now</span>
            {wallet.state === "live" ? (
              <>
                <strong>{formatMoneyNumber(wallet.totalBalance)}<small>{currencyCode}</small></strong>
                <small className="detail-tile-note">
                  {formatMoneyNumber(wallet.availableBalance)} available · read {relativeTime(wallet.observedAt)}
                </small>
              </>
            ) : (
              <>
                <strong className="is-unavailable">Unavailable</strong>
                <small className="detail-tile-note">{walletNote}</small>
              </>
            )}
          </div>
          <div className="detail-tile">
            <span>Budget per strategy</span>
            {budgetPreview ? (
              <>
                <strong>{formatMoneyNumber(budgetPreview.budgetPerStrategy)}<small>{currencyCode}</small></strong>
                <small className="detail-tile-note">
                  Next entry can use {formatMoneyNumber(budgetPreview.nextStrategyCanUse)} · up to {budgetPreview.maximumConcurrentStrategies} at once
                </small>
              </>
            ) : (
              <>
                <strong className="is-text">{allocationModeText(capitalPolicy.allocationMode)}</strong>
                <small className="detail-tile-note">The amount needs a live wallet balance.</small>
              </>
            )}
          </div>
        </div>
        <CapitalPolicyEditor
          userId={userId}
          name={displayName(profile)}
          policy={capitalPolicy}
          onNotice={onNotice}
          onSaved={() => setToken(value => value + 1)}
        />
        <CapitalHistory observations={capitalHistory} />
      </Panel>

      </details>

      <Panel className="report-panel">
        <PanelHeader title="Software trade performance" meta={`As of ${relativeTime(detail.data.asOf)}`} />
        <OwnerReportControls asset={asset} range={range} onAssetChange={setAsset} onRangeChange={setRange} />
        {!detail.data.historyComplete && (
          <p className="callout tone-warning" role="status">
            <AlertTriangle aria-hidden="true" />
            <span>Earlier runs are still being imported. Totals may grow once the import is verified.</span>
          </p>
        )}
        {scopeReady ? <PnlTiles
          summary={performance.ownerScope}
          showTotalRuns
          scopeNote={`Owner view: includes ${performance.ownerScope.deletedByUserRuns} ${performance.ownerScope.deletedByUserRuns === 1 ? "run" : "runs"} the user deleted. The user's own P&L shows ${formatMoneyNumber(userScope.netRealizedPnl, { signed: true })} ${currencyCode} across ${userScope.settledRuns} settled ${userScope.settledRuns === 1 ? "run" : "runs"}.`}
        /> : <TileSkeleton count={7} />}
      </Panel>

      <PnlCharts key={`${userId}:${asset}:${range}`} range={range} asset={asset} refreshToken={reportToken}
        endpoint={`${reportBase}/trades`} />
      <PnlHeatmap key={`${userId}:${asset}:calendar`} asset={asset} strategy={null} refreshToken={reportToken}
        endpoint={`${reportBase}/pnl/calendar`} />

      <Panel className="report-panel">
        <PanelHeader title="Software trades" meta="Deleted runs stay here, marked, after the user removes them." />
        <div className="report-filters">
          <StateSelect value={stateFilter} onChange={setStateFilter} />
          <Select
            label="User history"
            value={deleted}
            options={DELETED_OPTIONS}
            onChange={next => {
              const match = DELETED_OPTIONS.find(option => option.value === next);
              if (match) setDeleted(match.value);
            }}
          />
        </div>
        <TradeTable
          state={trades.state}
          showDeleted
          onInspect={setInspecting}
          onRetry={() => void trades.reload()}
          onLoadMore={() => void trades.loadMore()}
          emptyText="Runs this software places for this account will appear here."
        />
      </Panel>

      {inspecting && (
        <RunDetailDialog
          run={runStub(inspecting)}
          refreshToken={0}
          load={loadArchived}
          onClose={() => setInspecting(null)}
          notice={inspecting.deletedByUserAt ? (
            <InlineMessage tone="warning">
              Deleted from the user&apos;s history {formatTimestamp(inspecting.deletedByUserAt)}. This is the owner reporting copy.
            </InlineMessage>
          ) : (
            <InlineMessage tone="info">Read-only view of this account&apos;s run. Raw exchange responses are not shown.</InlineMessage>
          )}
        />
      )}
    </>
  );
}


/**
 * The owner's control over one account's per-strategy budget. The account holder
 * can still change it from Portfolio; whichever change is saved last applies to
 * the next entry. Runs already open keep the budget they entered with.
 */
function OwnerReportControls({ asset, range, onAssetChange, onRangeChange }: {
  asset: ReportAsset;
  range: ReportRange;
  onAssetChange: (value: ReportAsset) => void;
  onRangeChange: (value: ReportRange) => void;
}) {
  return <div className="owner-report-controls">
    <Segmented label="Performance asset" value={asset} options={REPORT_ASSET_OPTIONS.map(option => ({ ...option }))}
      onChange={value => {
        const match = REPORT_ASSET_OPTIONS.find(option => option.value === value);
        if (match) onAssetChange(match.value);
      }} />
    <RangeControl value={range} onChange={onRangeChange} />
  </div>;
}

function CapitalPolicyEditor({ userId, name, policy, onNotice, onSaved }: {
  userId: string;
  name: string;
  policy: OwnerUserDetail["capitalPolicy"];
  onNotice: NoticeHandler;
  onSaved: () => void;
}) {
  const { currencyCode, convertFromUsd, convertToUsd } = useCurrency();
  const savedMode: CapitalAllocationMode = isCapitalAllocationMode(policy.allocationMode) ? policy.allocationMode : "half_balance";
  const savedAmountUsd = toNumber(policy.capitalAmount);
  const [mode, setMode] = useState<CapitalAllocationMode>(savedMode);
  const [amount, setAmount] = useState(() => convertFromUsd(savedAmountUsd ?? 100));
  const [status, setStatus] = useState<{ kind: "idle" } | { kind: "saving" } | { kind: "error"; message: string }>({ kind: "idle" });

  const dirty = mode !== savedMode
    || (mode === "fixed_amount" && Math.abs((savedAmountUsd ?? 0) - convertToUsd(amount)) > 0.00001);
  const invalid = mode === "fixed_amount" && !(amount > 0);
  const options = CAPITAL_MODE_OPTIONS.map(option => option.value === "fixed_amount"
    ? { ...option, label: `Fixed ${currencyCode} amount` } : { ...option });

  async function save() {
    if (invalid || status.kind === "saving") return;
    setStatus({ kind: "saving" });
    try {
      await requestJson<{ capitalPolicy: OwnerUserDetail["capitalPolicy"] }>(
        `/api/owner/users/${encodeURIComponent(userId)}/capital`,
        {
          method: "PUT",
          body: JSON.stringify({ allocationMode: mode, capitalAmount: mode === "fixed_amount" ? convertToUsd(amount) : null })
        }
      );
      setStatus({ kind: "idle" });
      onNotice({ tone: "ok", text: `Budget per strategy saved for ${name}. It applies from the next entry.` });
      // Reload so the saved policy and its budget come from the server, not from this form.
      onSaved();
    } catch (error) {
      setStatus({ kind: "error", message: errorMessage(error) });
    }
  }

  return (
    <div className="owner-capital-editor">
      <div className="owner-capital-fields">
        <Select
          label="Budget per strategy"
          value={mode}
          options={options}
          onChange={value => { if (isCapitalAllocationMode(value)) setMode(value); }}
          hint={`Calculated from the account's total USD balance, capped by what is available. ${name} can also change this from Portfolio.`}
        />
        {mode === "fixed_amount" && (
          <NumberField label="Amount" value={amount} min={0.01} step={0.01} suffix={currencyCode} invalid={invalid} onChange={setAmount} />
        )}
      </div>
      <div className="owner-capital-actions">
        {dirty && (
          <>
            <button type="button" className="button ghost small" disabled={status.kind === "saving"}
              onClick={() => { setMode(savedMode); setAmount(convertFromUsd(savedAmountUsd ?? 100)); setStatus({ kind: "idle" }); }}>
              Reset
            </button>
            <button type="button" className="button primary small" disabled={invalid || status.kind === "saving"} onClick={() => void save()}>
              <Save aria-hidden="true" />{status.kind === "saving" ? "Saving…" : "Save budget"}
            </button>
          </>
        )}
        {!dirty && <small className="field-hint">Open runs keep the budget they entered with.</small>}
      </div>
      {status.kind === "error" && <InlineMessage tone="error">Budget was not changed. {status.message}</InlineMessage>}
    </div>
  );
}
