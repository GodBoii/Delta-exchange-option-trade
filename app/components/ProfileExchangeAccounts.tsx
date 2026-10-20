"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { ChevronDown, Copy, KeyRound, Layers3, RefreshCw } from "@/app/components/icons";
import { EmptyState, Field, InlineMessage, SectionHeading, Select, TableSkeleton } from "@/app/components/ui";
import { requestJson, type ApiRequester } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import {
  mode,
  object,
  parseOverview,
  parsePendingRuns,
  parseServerIp,
  parseSnapshot,
  participation,
  validTransferAmount,
  type AccountOverview,
  type AccountSnapshot,
  type ManagedAccount,
  type ManagementMode,
  type ServerIp,
} from "@/lib/subaccounts";

const BASE = "/api/profile/subaccounts";
const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const usd = (value: string) =>
  `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;

export function ProfileExchangeAccounts({
  connected,
  request = requestJson,
  onConnect,
}: {
  connected: boolean;
  request?: ApiRequester;
  onConnect?: () => void;
}) {
  return (
    <section
      className="profile-exchange"
      aria-label="Sub accounts"
    >
      <SectionHeading title="Sub accounts" />
      {connected ? (
        <Subaccounts request={request} />
      ) : (
        <>
          <EmptyState icon={<Layers3 />} title="Connect your main account first"
            description="Your main Delta connection is needed to discover sub accounts and manage their funds."
            action={onConnect && <button type="button" className="button primary" onClick={onConnect}><KeyRound aria-hidden="true" />Connect Delta</button>} />
          <Disclosure title="API setup" hint="Server IP for your Delta whitelist"><WhitelistIp request={request} /></Disclosure>
        </>
      )}
    </section>
  );
}

/** Uses the installed accordion recipe. Closed controls leave the tab order. */
function Disclosure({ title, hint, children, className = "", defaultOpen = false }: {
  title: string; hint?: string; children: ReactNode; className?: string; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className={`subaccount-disclosure t-acc ${className}`.trim()} data-open={open}>
      <h3>
        <button type="button" className="t-acc-head" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
          <span>{title}{hint && <small>{hint}</small>}</span>
          <span className="t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
        </button>
      </h3>
      <div className="t-acc-panel" id={id} aria-hidden={!open}>
        <div className="t-acc-panel-inner" inert={!open}>
          <div className="subaccount-disclosure-content">{children}</div>
        </div>
      </div>
    </section>
  );
}

function WhitelistIp({ request }: { request: ApiRequester }) {
  const [state, setState] = useState<ServerIp | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const alive = useRef(true);
  const refresh = useCallback(
    async (manual = false) => {
      setBusy(true);
      try {
        const next = parseServerIp(
          await request(
            `/api/profile/server-ip${manual ? "/refresh" : ""}`,
            manual ? json("POST") : undefined,
          ),
        );
        if (alive.current) {
          setState(next);
          setNote("");
        }
      } catch (error) {
        if (alive.current) setNote(errorMessage(error));
      } finally {
        if (alive.current) setBusy(false);
      }
    },
    [request],
  );
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 60_000);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);
  async function copy() {
    if (!state?.address) return;
    try {
      await navigator.clipboard.writeText(state.address);
      setNote("IP address copied.");
    } catch {
      setNote("Copy is unavailable. Select the address and copy it manually.");
    }
  }
  return (
    <div className="profile-ip">
      <div className="profile-exchange-heading">
        <h4>Server IP</h4>
        <button
          type="button"
          className="button ghost"
          disabled={busy}
          onClick={() => {
            void refresh(true);
          }}
          aria-label="Refresh server IP"
        >
          <RefreshCw className={busy ? "spin" : undefined} aria-hidden="true" />
        </button>
      </div>
      <div className="profile-ip-value">
        <code>
          {state?.address ??
            (busy ? "Checking address…" : "Address unavailable")}
        </code>
        <button
          type="button"
          className="button secondary"
          disabled={!state?.address}
          onClick={() => {
            void copy();
          }}
        >
          <Copy aria-hidden="true" />
          Copy
        </button>
      </div>
      <p className="muted">
        Add this server address to each Delta API key&apos;s IP whitelist.
      </p>
      {state?.checkedAt && (
        <small className="muted">
          Last checked {new Date(state.checkedAt).toLocaleString()}
          {state.stale ? ". Address is stale; refresh before using it." : ""}
        </small>
      )}
      <span role="status" className="profile-exchange-note">
        {note}
      </span>
    </div>
  );
}

function Subaccounts({ request }: { request: ApiRequester }) {
  const [data, setData] = useState<AccountOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState<{ id: string; name: string }[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const operation = useRef(false);
  const alive = useRef(true);
  const reload = useCallback(async () => {
    const next = parseOverview(await request(BASE));
    if (alive.current) setData(next);
  }, [request]);
  useEffect(() => {
    alive.current = true;
    setBusy(true);
    void reload()
      .then(async () => {
        const next = parseOverview(await request(`${BASE}/sync`, json("POST")));
        if (alive.current) setData(next);
      })
      .catch((error) => {
        if (alive.current) setError(errorMessage(error));
      })
      .finally(() => {
        if (alive.current) setBusy(false);
      });
    return () => {
      alive.current = false;
    };
  }, [reload, request]);
  async function action(work: () => Promise<unknown>, message: string) {
    if (busy || operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    setNote("");
    try {
      await work();
      await reload();
      setNote(message);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      operation.current = false;
      setBusy(false);
    }
  }
  async function loadPending() {
    await action(async () => {
      setPending(parsePendingRuns(await request("/api/strategies")));
    }, "Unassigned runs refreshed.");
  }
  async function loadHistory(more = false) {
    await action(async () => {
      const response = object(
        await request(
          `${BASE}/transfers/history${more && cursor ? `?after=${encodeURIComponent(cursor)}` : ""}`,
        ),
      );
      if (!Array.isArray(response.result))
        throw new Error("Transfer history is unavailable.");
      const rows = response.result.map((value) => {
        const row = object(value);
        return `${String(row.created_at)} · ${String(row.transferrer_user_id)} → ${String(row.transferee_user_id)} · ${String(row.amount)} ${String(row.asset_symbol)}`;
      });
      setHistory((previous) => (more ? [...previous, ...rows] : rows));
      const after = object(response.meta).after;
      setCursor(typeof after === "string" ? after : null);
    }, "Exchange transfer history refreshed.");
  }
  const unresolved = data?.transfers.filter(transfer => ["unknown", "submitting", "prepared"].includes(transfer.state)).length ?? 0;
  function accountLabel(id: string) {
    if (data?.mainAccount.id === id) return data.mainAccount.name;
    return data?.accounts.find(account => account.id === id)?.name ?? id;
  }
  return (
    <div className="profile-subaccounts">
      <div className="profile-exchange-heading">
        <div>
          <h2>Accounts{data && <span className="subaccount-count">{data.accounts.length}</span>}</h2>
          <p className="muted">
            {data ? `${data.capacity.available} available for automatic assignment${data.capacity.target !== null ? ` · Target ${data.capacity.target}` : ""}` : "Separate balances and positions for each strategy"}
          </p>
        </div>
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => {
            void action(
              () => request(`${BASE}/sync`, json("POST")),
              "Subaccounts refreshed.",
            );
          }}
        >
          <RefreshCw className={busy ? "spin" : undefined} aria-hidden="true" />
          {busy ? "Refreshing" : "Discover"}
        </button>
      </div>
      {error && <InlineMessage tone="error">{error}</InlineMessage>}
      <span role="status" className="profile-exchange-note">
        {note}
      </span>
      {!data ? (
        error ? <p className="muted">Accounts could not be loaded. Select Discover to retry.</p> : <TableSkeleton label="sub accounts" rows={3} />
      ) : (
        <div className="subaccounts-layout">
          <div className="subaccounts-main">
          {unresolved > 0 && <InlineMessage tone="warning">{unresolved} {unresolved === 1 ? "transfer needs" : "transfers need"} confirmation. Review Transfer activity before moving more funds.</InlineMessage>}
          {data.capacity.target !== null &&
            data.settings.mode === "automatic" &&
            data.capacity.available < data.capacity.target && (
              <p className="profile-capacity-warning">
                Connected idle accounts currently limit new isolated entries to{" "}
                {data.capacity.available}.
              </p>
            )}
          {data.accounts.length === 0 && (
            <EmptyState compact icon={<Layers3 />} title="No sub accounts yet"
              description="Create a sub account on Delta, then select Discover to connect its API key."
              action={<a className="button secondary" href="https://www.delta.exchange/support/solutions?articleId=80001177857" target="_blank" rel="noreferrer">Create on Delta</a>} />
          )}
          {data.accounts.length > 0 && <p className="muted subaccounts-help">
            <a
              href="https://www.delta.exchange/support/solutions?articleId=80001177857"
              target="_blank"
              rel="noreferrer"
            >
              Create on Delta
            </a>
            <span>Each account needs its own API key.</span>
          </p>}
          <div className="profile-account-list">
            {data.accounts.map((account) => (
              <SubaccountCard
                key={account.id}
                account={account}
                request={request}
                busy={busy}
                action={action}
                pending={pending}
                loadPending={loadPending}
              />
            ))}
          </div>
          </div>
          <aside className="subaccounts-tools" aria-label="Account settings and funding">
          <Disclosure title="Account management" hint={data.settings.mode === "main" ? "New entries use the main account" : data.settings.mode === "automatic" ? "Automatic sub account assignment" : "Manual run assignment"}>
            <ManagementForm data={data} busy={busy}
              save={(nextMode, autoFund) => action(() => request(`${BASE}/settings`, json("PUT", {
                mode: nextMode, autoFund, expectedRevision: data.settings.revision,
              })), "Account management saved.")} />
          </Disclosure>
          {data.accounts.length > 0 && (
            <TransferForm
              data={data}
              busy={busy}
              send={(body) =>
                action(
                  () => request(`${BASE}/transfers`, json("POST", body)),
                  "Transfer request recorded. Check its status below.",
                )
              }
            />
          )}
          <Disclosure title="Transfer activity" hint={unresolved > 0 ? `${unresolved} awaiting confirmation` : `${data.transfers.length} application ${data.transfers.length === 1 ? "transfer" : "transfers"}`}>
            {data.transfers.length === 0 && (
              <p className="muted">
                No transfers requested through this application.
              </p>
            )}
            {data.transfers.map((transfer) => (
              <div key={transfer.id} className="profile-transfer-row">
                <span>
                  {accountLabel(transfer.source_id)} → {accountLabel(transfer.target_id)}
                  <br />
                  <small>
                    {usd(transfer.amount)} · {transfer.state}
                  </small>
                  {transfer.error && <small>{transfer.error}</small>}
                </span>
                {["unknown", "submitting", "prepared"].includes(
                  transfer.state,
                ) && (
                  <button
                    className="button secondary"
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      void action(
                        () =>
                          request(
                            `${BASE}/transfers/${transfer.id}/reconcile`,
                            json("POST"),
                          ),
                        "Transfer reconciled.",
                      );
                    }}
                  >
                    Reconcile
                  </button>
                )}
              </div>
            ))}
            <button
              className="button ghost"
              type="button"
              disabled={busy}
              onClick={() => {
                void loadHistory();
              }}
            >
              Load Delta transfer history
            </button>
            {history.map((line, index) => (
              <p className="profile-transfer-history" key={`${index}-${line}`}>
                {line}
              </p>
            ))}
            {cursor && (
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => {
                  void loadHistory(true);
                }}
              >
                Load more
              </button>
            )}
          </Disclosure>
          <Disclosure title="API setup" hint="Server IP and key permissions">
            <WhitelistIp request={request} />
          </Disclosure>
          </aside>
        </div>
      )}
    </div>
  );
}

function ManagementForm({
  data,
  busy,
  save,
}: {
  data: AccountOverview;
  busy: boolean;
  save: (mode: ManagementMode, autoFund: boolean) => Promise<void>;
}) {
  const [selected, setSelected] = useState(data.settings.mode);
  const [autoFund, setAutoFund] = useState(data.settings.autoFund);
  useEffect(() => {
    setSelected(data.settings.mode);
    setAutoFund(data.settings.autoFund);
  }, [data.settings.mode, data.settings.autoFund]);
  return (
    <form
      className="profile-management"
      onSubmit={(event) => {
        event.preventDefault();
        void save(selected, autoFund);
      }}
    >
      <Select
        label="New strategy entries"
        value={selected}
        disabled={busy}
        onChange={value => setSelected(mode(value))}
        options={[
          { value: "main", label: "Use main account" },
          { value: "automatic", label: "Assign subaccounts automatically" },
          { value: "manual", label: "Assign each run manually" },
        ]}
      />
      <label className="profile-checkbox">
        <input
          type="checkbox"
          checked={autoFund}
          disabled={busy || selected === "main"}
          onChange={(event) => setAutoFund(event.target.checked)}
        />
        Automatically transfer required funds before entry
      </label>
      <small className="muted">
        Capital percentages apply to the combined managed pool once. Existing
        positions keep their account and exit rules.
      </small>
      <button
        className="button secondary"
        type="submit"
        disabled={
          busy ||
          (selected === data.settings.mode &&
            autoFund === data.settings.autoFund)
        }
      >
        Save account management
      </button>
    </form>
  );
}

type Action = (work: () => Promise<unknown>, message: string) => Promise<void>;
function SubaccountCard({
  account,
  request,
  busy,
  action,
  pending,
  loadPending,
}: {
  account: ManagedAccount;
  request: ApiRequester;
  busy: boolean;
  action: Action;
  pending: { id: string; name: string }[];
  loadPending: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<AccountSnapshot | null>(null);
  const [snapshotError, setSnapshotError] = useState("");
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [strategy, setStrategy] = useState("");
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const snapshotGeneration = useRef(0);
  const invalidateSnapshot = useCallback(() => { snapshotGeneration.current++; }, []);
  const id = useId();
  const refresh = useCallback(async () => {
    const generation = ++snapshotGeneration.current;
    setSnapshotBusy(true);
    try {
      const next = parseSnapshot(await request(`${BASE}/${account.id}/snapshot`));
      if (generation !== snapshotGeneration.current) return;
      setSnapshot(next);
      setSnapshotError("");
    } catch (error) {
      if (generation === snapshotGeneration.current) setSnapshotError(errorMessage(error));
    } finally {
      if (generation === snapshotGeneration.current) setSnapshotBusy(false);
    }
  }, [account.id, request]);
  useEffect(() => {
    if (!open || !account.connected) return;
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 15_000);
    return () => {
      window.clearInterval(timer);
      invalidateSnapshot();
    };
  }, [open, account.connected, refresh, invalidateSnapshot]);
  const mutation = (
    url: string,
    method: string,
    body: unknown,
    message: string,
  ) => action(() => request(url, json(method, body)), message);
  function connect(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      await request(
        `${BASE}/${account.id}/key`,
        json("PUT", { apiKey: key, apiSecret: secret }),
      );
      setKey("");
      setSecret("");
    }, "Subaccount API access verified.");
  }
  return (
    <article className="profile-subaccount t-acc" data-open={open}>
      <button
        type="button"
        className="profile-subaccount-toggle"
        aria-expanded={open}
        aria-controls={`${id}-body`}
        onClick={() => {
          setOpen(!open);
          setKey("");
          setSecret("");
        }}
      >
        <span>
          <strong>{account.name}</strong>
          <small>
            ID {account.id} · {account.marginMode ?? "Margin unknown"}
          </small>
        </span>
        <span className="profile-account-status" data-tone={!account.present || !account.connected ? "warning" : account.busy ? "neutral" : "active"}>
          <span>
          {!account.present
            ? "Unavailable on Delta"
            : !account.connected
              ? "Connect API key"
              : account.busy
                ? "Reserved"
                : "Connected"}
          </span>
          <small>{account.busy ? account.strategyName ?? "Run assigned" : account.participation === "automatic" ? "Automatic" : account.participation === "manual" ? "Manual" : "New entries disabled"}</small>
        </span>
        <span className="t-acc-chevron" aria-hidden="true"><ChevronDown /></span>
      </button>
      <div className="t-acc-panel" id={`${id}-body`} aria-hidden={!open}>
        <div className="t-acc-panel-inner" inert={!open}>
        <div className="profile-subaccount-body">
          {account.connected && <div className="profile-wallet" aria-busy={snapshotBusy}>
            <span>Balance<strong>{snapshot ? usd(snapshot.balance) : "—"}</strong></span>
            <span>Available<strong>{snapshot ? usd(snapshot.available) : "—"}</strong></span>
            <span>Recorded realized P&L<strong>{snapshot && snapshot.realizedPnl !== null ? usd(snapshot.realizedPnl) : "—"}</strong></span>
          </div>}
          <Select
            label="Participation"
            value={account.participation}
            disabled={busy || !account.connected || !account.present}
            onChange={value => {
              void mutation(
                `${BASE}/${account.id}/participation`,
                "PUT",
                { participation: participation(value) },
                "Subaccount participation saved.",
              );
            }}
            options={[
              { value: "disabled", label: "Disabled for new entries" },
              { value: "manual", label: "Manual assignment only" },
              { value: "automatic", label: "Available for automatic assignment" },
            ]}
          />
          {account.busy && (
            <p>
              Assigned to {account.strategyName ?? "an existing run"}
              {account.budget ? ` · ${usd(account.budget)} allocated` : ""}.
              Disabling new entries keeps its exits running.
            </p>
          )}
          {account.strategyId && (
            <button
              type="button"
              className="button ghost"
              disabled={busy}
              onClick={() => {
                void mutation(
                  `${BASE}/assignments/${account.strategyId}/release`,
                  "POST",
                  undefined,
                  "Account released after flat verification.",
                );
              }}
            >
              Release finished account
            </button>
          )}
          {account.connected && (
            <>
              <button
                type="button"
                className="button ghost"
                disabled={busy || snapshotBusy}
                onClick={() => {
                  void refresh();
                }}
              >
                <RefreshCw className={snapshotBusy ? "spin" : undefined} aria-hidden="true" />
                {snapshotBusy ? "Refreshing account" : "Refresh account"}
              </button>
              {snapshotError && (
                <InlineMessage tone="error">{snapshotError}</InlineMessage>
              )}
              {snapshot && (
                <div className="profile-exposure">
                  <h4>Positions</h4>
                  {snapshot.positions
                    .filter((position) => Number(position.size) !== 0)
                    .map((position) => (
                      <div
                        className="profile-transfer-row"
                        key={position.productId}
                      >
                        <span>
                          {position.symbol}
                          <small>
                            {position.size} lots
                            {position.pnl !== null
                              ? ` · P&L ${usd(position.pnl)}`
                              : ""}
                          </small>
                        </span>
                        <button
                          type="button"
                          className="button danger"
                          disabled={busy}
                          onClick={() => {
                            if (
                              window.confirm(
                                `Close the position in ${account.name}: ${position.symbol}?`,
                              )
                            )
                              void action(async () => {
                                await request(
                                  `/api/positions/${position.productId}/close?accountId=${account.id}`,
                                  json("POST", { confirm: true }),
                                );
                                await refresh();
                              }, "Position close submitted and verified.");
                          }}
                        >
                          Close
                        </button>
                      </div>
                    ))}
                  {!snapshot.positions.some(
                    (position) => Number(position.size) !== 0,
                  ) && <p className="muted">No open positions.</p>}
                  <h4>Open orders</h4>
                  {snapshot.orders.map((order) => (
                    <div className="profile-transfer-row" key={order.id}>
                      <span>
                        {order.symbol}
                        <small>
                          {order.side} · {order.size} lots
                        </small>
                      </span>
                      <button
                        type="button"
                        className="button secondary"
                        disabled={busy}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Cancel order ${order.id} in ${account.name}?`,
                            )
                          )
                            void action(async () => {
                              await request(
                                `/api/orders/${order.id}?accountId=${account.id}`,
                                json("DELETE", {
                                  productId: order.productId,
                                  confirm: true,
                                }),
                              );
                              await refresh();
                            }, "Order cancelled.");
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ))}
                  {snapshot.orders.length === 0 && (
                    <p className="muted">No open orders.</p>
                  )}
                </div>
              )}
              {!account.busy && (
                <Disclosure className="profile-assignment" title="Assign a run">
                  <div className="stack">
                  <button
                    type="button"
                    className="button ghost"
                    disabled={busy}
                    onClick={() => {
                      void loadPending();
                    }}
                  >
                    Load unassigned runs
                  </button>
                  <Select
                    label="Run to assign"
                    value={strategy}
                    onChange={setStrategy}
                    disabled={busy || pending.length === 0}
                    hint={pending.length === 0 ? "Load unassigned runs to check for available drafts and scheduled runs." : undefined}
                    options={[
                      { value: "", label: "Select a draft or scheduled run", disabled: true },
                      ...pending.map(run => ({ value: run.id, label: run.name })),
                    ]}
                  />
                  <button
                    className="button secondary"
                    type="button"
                    disabled={
                      busy || !strategy || account.participation === "disabled"
                    }
                    onClick={() => {
                      void mutation(
                        `${BASE}/assignments`,
                        "POST",
                        { strategyId: strategy, accountId: account.id },
                        "Run assigned permanently to this subaccount.",
                      );
                    }}
                  >
                    Assign run
                  </button>
                  <small className="muted">Assignment is permanent. Cancel the run to choose a different account.</small>
                  </div>
                </Disclosure>
              )}
            </>
          )}
          <Disclosure className="profile-key-form" title={account.connected ? "Replace API key" : "Connect API key"} defaultOpen={!account.connected}>
            <form onSubmit={connect} className="stack profile-credentials">
              <Field label="API key">
                <input
                  id={`${id}-key`}
                  autoComplete="off"
                  value={key}
                  minLength={16}
                  maxLength={128}
                  required
                  disabled={busy || !account.present}
                  onChange={(event) => setKey(event.target.value)}
                />
              </Field>
              <Field label="API secret">
                <input
                  id={`${id}-secret`}
                  type="password"
                  autoComplete="new-password"
                  value={secret}
                  minLength={24}
                  maxLength={256}
                  required
                  disabled={busy || !account.present}
                  onChange={(event) => setSecret(event.target.value)}
                />
              </Field>
              <button
                type="submit"
                className="button secondary"
                disabled={busy || !account.present}
              >
                Verify and save key
              </button>
              <small className="muted">
                Enable Read Data and Trading on Delta. Saved secrets are
                encrypted and never displayed.
              </small>
            </form>
          {account.connected && (
            <button
              type="button"
              className="button danger"
              disabled={busy || account.busy}
              onClick={() => {
                if (
                  window.confirm(
                    `Remove the stored API key for ${account.name}?`,
                  )
                )
                  void mutation(
                    `${BASE}/${account.id}/key`,
                    "DELETE",
                    undefined,
                    "Subaccount key removed.",
                  );
              }}
            >
              Remove API key
            </button>
          )}
          </Disclosure>
        </div>
        </div>
      </div>
    </article>
  );
}

function TransferForm({
  data,
  busy,
  send,
}: {
  data: AccountOverview;
  busy: boolean;
  send: (body: unknown) => Promise<void>;
}) {
  const [source, setSource] = useState(data.mainAccount.id);
  const [target, setTarget] = useState("");
  const [amount, setAmount] = useState("");
  const requestId = useRef<string | null>(null);
  const accounts = [
    data.mainAccount,
    ...data.accounts.filter(
      (account) => account.present && account.parentId === data.mainAccount.id,
    ),
  ];
  const changed = () => {
    requestId.current = null;
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!validTransferAmount(amount) || source === target || !target) return;
    if (
      !window.confirm(
        `Transfer ${amount} USD from account ${source} to ${target}?`,
      )
    )
      return;
    requestId.current ??= crypto.randomUUID();
    await send({
      requestId: requestId.current,
      sourceId: source,
      targetId: target,
      amount,
      confirm: true,
    });
  }
  return (
    <Disclosure title="Transfer funds" hint="Move USD between your accounts">
      <form
        className="profile-management"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <Select
          label="From"
          value={source}
          disabled={busy}
          onChange={value => {
            setSource(value);
            if (value === target) setTarget("");
            changed();
          }}
          options={accounts.map(account => ({ value: account.id, label: account.name, hint: `Account ${account.id}` }))}
        />
        <Select
          label="To"
          value={target}
          disabled={busy}
          onChange={value => {
            setTarget(value);
            changed();
          }}
          options={[
            { value: "", label: "Select account", disabled: true },
            ...accounts.filter(account => account.id !== source)
              .map(account => ({ value: account.id, label: account.name, hint: `Account ${account.id}` })),
          ]}
        />
        <Field label="Amount in USD" className="field-number">
          <input
            inputMode="decimal"
            value={amount}
            required
            placeholder="0.00"
            disabled={busy}
            onChange={(event) => {
              setAmount(event.target.value);
              changed();
            }}
          />
        </Field>
        <small className="muted">
          The source must have no positions, orders or reserved run. Uncertain
          transfers block further funding until reconciled.
        </small>
        <button
          className="button secondary"
          type="submit"
          disabled={
            busy || !target || source === target || !validTransferAmount(amount)
          }
        >
          Transfer USD
        </button>
      </form>
    </Disclosure>
  );
}
