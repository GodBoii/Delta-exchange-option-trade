"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Copy, RefreshCw } from "@/app/components/icons";
import { InlineMessage } from "@/app/components/ui";
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
  `${Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 })} USD`;

export function ProfileExchangeAccounts({
  connected,
  request = requestJson,
}: {
  connected: boolean;
  request?: ApiRequester;
}) {
  return (
    <section
      className="profile-exchange"
      aria-label="Delta API and subaccounts"
    >
      <WhitelistIp request={request} />
      {connected ? (
        <Subaccounts request={request} />
      ) : (
        <p className="muted">
          Connect your main Delta account to discover and manage subaccounts.
        </p>
      )}
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
        <h3>Server IP for Delta API whitelist</h3>
        <button
          type="button"
          className="button ghost"
          disabled={busy}
          onClick={() => {
            void refresh(true);
          }}
          aria-label="Refresh server IP"
        >
          <RefreshCw aria-hidden="true" />
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
  const alive = useRef(true);
  const reload = useCallback(async () => {
    const next = parseOverview(await request(BASE));
    if (alive.current) setData(next);
  }, [request]);
  useEffect(() => {
    alive.current = true;
    void reload()
      .then(async () => {
        const next = parseOverview(await request(`${BASE}/sync`, json("POST")));
        if (alive.current) setData(next);
      })
      .catch((error) => {
        if (alive.current) setError(errorMessage(error));
      });
    return () => {
      alive.current = false;
    };
  }, [reload, request]);
  async function action(work: () => Promise<unknown>, message: string) {
    if (busy) return;
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
  return (
    <div className="profile-subaccounts">
      <div className="profile-exchange-heading">
        <div>
          <h3>Subaccounts</h3>
          <p className="muted">
            Separate balances and positions for each strategy.
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
          <RefreshCw aria-hidden="true" />
          Discover
        </button>
      </div>
      {error && <InlineMessage tone="error">{error}</InlineMessage>}
      <span role="status" className="profile-exchange-note">
        {note}
      </span>
      {!data ? (
        <p className="muted">
          {error ? "Subaccounts could not be loaded." : "Loading subaccounts…"}
        </p>
      ) : (
        <>
          <ManagementForm
            data={data}
            busy={busy}
            save={(nextMode, autoFund) =>
              action(
                () =>
                  request(
                    `${BASE}/settings`,
                    json("PUT", {
                      mode: nextMode,
                      autoFund,
                      expectedRevision: data.settings.revision,
                    }),
                  ),
                "Account management saved.",
              )
            }
          />
          <p className="muted">
            {data.capacity.available} available automatic accounts
            {data.capacity.target !== null
              ? ` · Target ${data.capacity.target} slots from your capital settings`
              : ""}
            . Availability is verified again before entry.
          </p>
          {data.capacity.target !== null &&
            data.settings.mode === "automatic" &&
            data.capacity.available < data.capacity.target && (
              <p className="profile-capacity-warning">
                Connected idle accounts currently limit new isolated entries to{" "}
                {data.capacity.available}.
              </p>
            )}
          {data.accounts.length === 0 && (
            <p>
              No subaccounts found. Create one on Delta, then select Discover.
            </p>
          )}
          <p className="muted">
            <a
              href="https://www.delta.exchange/support/solutions?articleId=80001177857"
              target="_blank"
              rel="noreferrer"
            >
              Create subaccounts on Delta
            </a>
            . Create each account&apos;s API key on Delta once, then connect it
            below.
          </p>
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
          <details className="profile-transfer-log">
            <summary>Transfer activity</summary>
            {data.transfers.length === 0 && (
              <p className="muted">
                No transfers requested through this application.
              </p>
            )}
            {data.transfers.map((transfer) => (
              <div key={transfer.id} className="profile-transfer-row">
                <span>
                  {transfer.source_id} → {transfer.target_id}
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
          </details>
        </>
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
  const id = useId();
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
      <label htmlFor={`${id}-mode`}>New strategy entries</label>
      <select
        id={`${id}-mode`}
        value={selected}
        disabled={busy}
        onChange={(event) => setSelected(mode(event.target.value))}
      >
        <option value="main">Use main account</option>
        <option value="automatic">Assign subaccounts automatically</option>
        <option value="manual">Assign each run manually</option>
      </select>
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
  const id = useId();
  const refresh = useCallback(async () => {
    try {
      setSnapshot(
        parseSnapshot(await request(`${BASE}/${account.id}/snapshot`)),
      );
      setSnapshotError("");
    } catch (error) {
      setSnapshotError(errorMessage(error));
    }
  }, [account.id, request]);
  useEffect(() => {
    if (!open || !account.connected) return;
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [open, account.connected, refresh]);
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
    <article className="profile-subaccount">
      <button
        type="button"
        className="profile-subaccount-toggle"
        aria-expanded={open}
        aria-controls={`${id}-body`}
        onClick={() => setOpen(!open)}
      >
        <span>
          <strong>{account.name}</strong>
          <small>
            ID {account.id} · {account.marginMode ?? "Margin unknown"}
          </small>
        </span>
        <span className="profile-account-status">
          {!account.present
            ? "Unavailable on Delta"
            : !account.connected
              ? "API key needed"
              : account.busy
                ? "Reserved"
                : "Connected"}
          <small>{open ? "Hide details" : "Manage"}</small>
        </span>
      </button>
      {open && (
        <div className="profile-subaccount-body" id={`${id}-body`}>
          <label htmlFor={`${id}-participation`}>Participation</label>
          <select
            id={`${id}-participation`}
            value={account.participation}
            disabled={busy || !account.connected || !account.present}
            onChange={(event) => {
              void mutation(
                `${BASE}/${account.id}/participation`,
                "PUT",
                { participation: participation(event.target.value) },
                "Subaccount participation saved.",
              );
            }}
          >
            <option value="disabled">Disabled for new entries</option>
            <option value="manual">Manual assignment only</option>
            <option value="automatic">
              Available for automatic assignment
            </option>
          </select>
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
              <div className="profile-wallet">
                <span>
                  Balance
                  <strong>{snapshot ? usd(snapshot.balance) : "—"}</strong>
                </span>
                <span>
                  Available
                  <strong>{snapshot ? usd(snapshot.available) : "—"}</strong>
                </span>
                <span>
                  Recorded realized P&L
                  <strong>
                    {snapshot?.realizedPnl ? usd(snapshot.realizedPnl) : "—"}
                  </strong>
                </span>
              </div>
              <button
                type="button"
                className="button ghost"
                disabled={busy}
                onClick={() => {
                  void refresh();
                }}
              >
                Refresh balances and positions
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
                <div className="profile-assignment">
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
                  <label htmlFor={`${id}-run`}>Run to assign</label>
                  <select
                    id={`${id}-run`}
                    value={strategy}
                    onChange={(event) => setStrategy(event.target.value)}
                    disabled={busy}
                  >
                    <option value="">Select a draft or scheduled run</option>
                    {pending.map((run) => (
                      <option value={run.id} key={run.id}>
                        {run.name}
                      </option>
                    ))}
                  </select>
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
                </div>
              )}
            </>
          )}
          <details className="profile-key-form">
            <summary>
              {account.connected ? "Replace API key" : "Connect API key"}
            </summary>
            <form onSubmit={connect} className="stack">
              <label htmlFor={`${id}-key`}>API key</label>
              <input
                id={`${id}-key`}
                autoComplete="off"
                value={key}
                minLength={16}
                maxLength={128}
                required
                onChange={(event) => setKey(event.target.value)}
              />
              <label htmlFor={`${id}-secret`}>API secret</label>
              <input
                id={`${id}-secret`}
                type="password"
                autoComplete="new-password"
                value={secret}
                minLength={24}
                maxLength={256}
                required
                onChange={(event) => setSecret(event.target.value)}
              />
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
          </details>
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
        </div>
      )}
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
  const id = useId();
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
    <details className="profile-funding">
      <summary>Transfer funds manually</summary>
      <form
        className="profile-management"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <label htmlFor={`${id}-source`}>From</label>
        <select
          id={`${id}-source`}
          value={source}
          disabled={busy}
          onChange={(event) => {
            setSource(event.target.value);
            changed();
          }}
        >
          {accounts.map((account) => (
            <option value={account.id} key={account.id}>
              {account.name} · {account.id}
            </option>
          ))}
        </select>
        <label htmlFor={`${id}-target`}>To</label>
        <select
          id={`${id}-target`}
          value={target}
          required
          disabled={busy}
          onChange={(event) => {
            setTarget(event.target.value);
            changed();
          }}
        >
          <option value="">Select account</option>
          {accounts
            .filter((account) => account.id !== source)
            .map((account) => (
              <option value={account.id} key={account.id}>
                {account.name} · {account.id}
              </option>
            ))}
        </select>
        <label htmlFor={`${id}-amount`}>Amount in USD</label>
        <input
          id={`${id}-amount`}
          inputMode="decimal"
          value={amount}
          required
          placeholder="0.00"
          onChange={(event) => {
            setAmount(event.target.value);
            changed();
          }}
        />
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
    </details>
  );
}
