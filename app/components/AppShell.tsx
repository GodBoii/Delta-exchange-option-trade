"use client";

import {
  useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode
} from "react";
import {
  Activity, BarChart3, Bot, ChevronDown, KeyRound, Layers3, LogOut, Newspaper, PieChart, Search,
  ThemeDark, ThemeLight, ThemeSystem
} from "@/app/components/icons";
import { useTheme, type ThemeChoice } from "@/app/components/theme";
import { useCurrency, type DisplayCurrency } from "@/app/components/currency";
import {
  Badge, Brand, StatusDot, SwapText, Tooltip, useDisclosure, useSlidingPill
} from "@/app/components/ui";

export type Tab = "connect" | "builder" | "market" | "news" | "automation" | "dashboard" | "runs";

/** `short` is the label under the icon in the phone dock, where ~56px is all a destination gets. */
type NavItem = { id: Tab; label: string; short: string; hint: string; icon: ReactNode };

/**
 * Navigation.
 *
 * One horizontal strip rather than a left rail. The rail was costing 268px of
 * permanent width on every screen for seven destinations, and a trading surface
 * wants that width for columns of figures. `family` splits execution from
 * read-only research, which is the distinction that matters here: an operator
 * browsing analysis should never land on a surface that can reach the exchange
 * without crossing a visible boundary first.
 *
 * Capital allocation is deliberately absent. Setting one account-wide budget is
 * a property of the portfolio, not a destination, so it lives inside Portfolio
 * next to the balance it is calculated from.
 */
const NAV_ITEMS: (NavItem & { family: "execute" | "research" })[] = [
  { id: "connect", label: "Connection", short: "Connect", hint: "Enable live execution", icon: <KeyRound />, family: "execute" },
  { id: "builder", label: "Builder", short: "Build", hint: "Configure and schedule", icon: <Layers3 />, family: "execute" },
  { id: "runs", label: "History", short: "History", hint: "Scheduled and active strategies", icon: <Activity />, family: "execute" },
  { id: "dashboard", label: "Portfolio", short: "Portfolio", hint: "Balances, positions and capital", icon: <PieChart />, family: "execute" },
  { id: "market", label: "Market", short: "Market", hint: "Order flow and volatility", icon: <BarChart3 />, family: "research" },
  { id: "news", label: "News", short: "News", hint: "Headlines and market impact", icon: <Newspaper />, family: "research" },
  { id: "automation", label: "Automation", short: "Agent", hint: "Agent reviews and proposals", icon: <Bot />, family: "research" }
];

/** Reading order of the sections, so a transition knows which way it travelled. */
export const TAB_ORDER: readonly Tab[] = NAV_ITEMS.map(item => item.id);

export type ConnectionState = {
  label: string;
  detail: string;
  tone: "active" | "warning" | "neutral";
};

export function AppShell({ tab, availableTabs, connection, account, badges, onNavigate, onDisconnect, onSignOut, banner, children }: {
  tab: Tab;
  /** Tabs appear only when their backend and account prerequisites are met. */
  availableTabs: Tab[];
  connection: ConnectionState;
  account: { name: string; detail: string };
  /** Counts worth surfacing on the navigation itself, keyed by tab. */
  badges?: Partial<Record<Tab, number>>;
  onNavigate: (tab: Tab) => void;
  onDisconnect?: () => void;
  onSignOut: () => void;
  banner?: ReactNode;
  children: ReactNode;
}) {
  const items = NAV_ITEMS.filter(item => availableTabs.includes(item.id));
  const pillKey = `${tab}:${availableTabs.join(",")}`;
  const { barRef, pill } = useSlidingPill(pillKey, '[aria-current="page"]');
  const dockPill = useSlidingPill(pillKey, '[aria-current="page"]');
  const activeRef = useRef<HTMLButtonElement>(null);
  const [commandOpen, setCommandOpen] = useState(false);

  /* Ctrl+K / Cmd+K opens the jump menu from anywhere, including inside a field. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(value => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /**
   * The strip scrolls horizontally on a narrow viewport, so the current section
   * is pulled into view. Without this a mobile user landing on the last
   * destination sees a strip that appears to start somewhere else.
   */
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [tab]);

  /** A visible boundary between the execution and research halves of the strip. */
  const firstResearch = items.find(item => item.family === "research")?.id;

  return (
    <div className="shell">
      <a className="skip-link" href="#workspace">Skip to main content</a>

      <header className="topbar">
        <div className="topbar-row">
          <Brand subtitle="" />

          <Tooltip label={connection.detail} placement="bottom">
            <span className={`live-pill tone-${connection.tone}`}>
              <StatusDot tone={connection.tone} />
              {/* The label changes with the connection, so it swaps in place
                  rather than being replaced between frames. */}
              <span className="live-pill-text"><SwapText>{connection.label}</SwapText></span>
            </span>
          </Tooltip>

          <div className="topbar-actions">
            <button
              type="button"
              className="command-trigger"
              aria-label="Jump to a section"
              aria-haspopup="dialog"
              onClick={() => setCommandOpen(true)}
            >
              <Search aria-hidden="true" />
              <span>Jump to</span>
              <kbd aria-hidden="true">Ctrl K</kbd>
            </button>
            <Clock />
            <AccountMenu
              account={account}
              connection={connection}
              onDisconnect={onDisconnect}
              onSignOut={onSignOut}
            />
          </div>
        </div>

        <nav className="topnav" aria-label="Dashboard sections">
          <div className="topnav-track" ref={barRef}>
            {pill}
            {items.map(item => {
              const count = badges?.[item.id] ?? 0;
              const current = item.id === tab;
              return (
                <button
                  type="button"
                  key={item.id}
                  ref={current ? activeRef : undefined}
                  className="topnav-item"
                  aria-label={item.label}
                  data-divider={item.id === firstResearch ? "before" : undefined}
                  aria-current={current ? "page" : undefined}
                  onClick={() => onNavigate(item.id)}
                >
                  <span className="topnav-item-icon" aria-hidden="true">
                    {item.icon}
                    {/* Only the badge slides and pops; the row it sits on never
                        moves, so the strip cannot shift under the cursor when a
                        run starts needing attention. */}
                    <Badge
                      count={count}
                      tone="negative"
                      label={`${count} ${item.label.toLowerCase()} ${count === 1 ? "item needs" : "items need"} attention`}
                    />
                  </span>
                  <span className="topnav-item-label">{item.label}</span>
                </button>
              );
            })}
          </div>
        </nav>
      </header>

      <main className="workspace" id="workspace">
        {banner}
        {children}
      </main>

      {/* Phones get the destinations at the bottom edge, inside thumb reach and
          with a label under every icon. It replaces the top strip below 720px
          (CSS hides one or the other), so only one is ever in the a11y tree. */}
      <nav className="dock" aria-label="Dashboard sections">
        <div className="dock-track" ref={dockPill.barRef}>
          {dockPill.pill}
          {items.map(item => {
            const count = badges?.[item.id] ?? 0;
            return (
              <button
                type="button"
                key={item.id}
                className="dock-item"
                aria-current={item.id === tab ? "page" : undefined}
                onClick={() => onNavigate(item.id)}
              >
                <span className="dock-item-icon" aria-hidden="true">
                  {item.icon}
                  <Badge count={count} tone="negative" label="" />
                </span>
                <span className="dock-item-label">{item.short}</span>
                {count > 0 && <span className="visually-hidden">, {count} need attention</span>}
              </button>
            );
          })}
        </div>
      </nav>

      {commandOpen && (
        <CommandMenu
          items={items}
          current={tab}
          onNavigate={next => { setCommandOpen(false); onNavigate(next); }}
          onSignOut={() => { setCommandOpen(false); onSignOut(); }}
          onClose={() => setCommandOpen(false)}
        />
      )}
    </div>
  );
}

type Command = { id: string; label: string; hint: string; icon: ReactNode; run: () => void };

/**
 * Jump menu.
 *
 * A filtered list with one active row, driven from the input so focus never
 * leaves the field: arrows move the active row, Enter runs it, Escape closes.
 * The list follows the combobox pattern, so screen readers announce the active
 * option through `aria-activedescendant` while the caret stays in the input.
 */
function CommandMenu({ items, current, onNavigate, onSignOut, onClose }: {
  items: NavItem[];
  current: Tab;
  onNavigate: (tab: Tab) => void;
  onSignOut: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  const commands = useMemo<Command[]>(() => [
    ...items.map(item => ({
      id: item.id,
      label: item.label,
      hint: item.id === current ? "You are here" : item.hint,
      icon: item.icon,
      run: () => onNavigate(item.id)
    })),
    { id: "sign-out", label: "Sign out", hint: "End this session", icon: <LogOut />, run: onSignOut }
  ], [items, current, onNavigate, onSignOut]);

  const needle = query.trim().toLowerCase();
  const visible = needle
    ? commands.filter(command => `${command.label} ${command.hint}`.toLowerCase().includes(needle))
    : commands;
  const activeIndex = Math.min(active, Math.max(visible.length - 1, 0));

  /* Focus moves into the field on open and back to whatever had it on close. */
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => previous?.focus?.();
  }, []);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!visible.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((activeIndex + step + visible.length) % visible.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      visible[activeIndex]?.run();
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  };

  return (
    <div className="command-layer" onPointerDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="command-panel" role="dialog" aria-modal="true" aria-label="Jump to a section">
        <div className="command-field">
          <Search aria-hidden="true" />
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={visible[activeIndex] ? `${listId}-${visible[activeIndex].id}` : undefined}
            aria-autocomplete="list"
            placeholder="Search sections and actions"
            value={query}
            onChange={event => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
          />
          <kbd aria-hidden="true">Esc</kbd>
        </div>
        <ul className="command-list" role="listbox" id={listId} aria-label="Results">
          {visible.map((command, index) => (
            <li
              key={command.id}
              id={`${listId}-${command.id}`}
              role="option"
              aria-selected={index === activeIndex}
              className="command-option"
              onPointerMove={() => { if (index !== activeIndex) setActive(index); }}
              onClick={command.run}
            >
              <span className="command-option-icon" aria-hidden="true">{command.icon}</span>
              <span className="command-option-text">
                <strong>{command.label}</strong>
                <small>{command.hint}</small>
              </span>
            </li>
          ))}
          {!visible.length && <li className="command-empty" role="presentation">No section matches &ldquo;{query}&rdquo;</li>}
        </ul>
      </div>
    </div>
  );
}

/**
 * Entry and exit times are configured in local time, so the shell states the
 * clock it is using instead of leaving the operator to assume it.
 */
function Clock() {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  if (!now) return <div className="context-clock" aria-hidden="true" />;
  const zone = new Intl.DateTimeFormat(undefined, { hour: "2-digit", timeZoneName: "short" })
    .formatToParts(now)
    .find(part => part.type === "timeZoneName")?.value ?? "Local";

  return (
    <Tooltip label="Entry and exit times use this clock" placement="bottom">
      <div className="context-clock">
        <time dateTime={now.toISOString()}>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
        <small>{zone}</small>
      </div>
    </Tooltip>
  );
}

const APPEARANCE_OPTIONS: { value: ThemeChoice; label: string; icon: ReactNode }[] = [
  { value: "system", label: "System", icon: <ThemeSystem /> },
  { value: "light", label: "Light", icon: <ThemeLight /> },
  { value: "dark", label: "Dark", icon: <ThemeDark /> }
];

const CURRENCY_OPTIONS: { value: DisplayCurrency; label: string; symbol: string }[] = [
  { value: "USD", label: "Dollar", symbol: "$" },
  { value: "INR", label: "Rupees", symbol: "₹" }
];

/**
 * Profile.
 *
 * Identity, the live connection state, appearance, and the two ways out, in one
 * place. Appearance is a three-option segmented control rather than a sun-moon
 * switch, because following the operating system is a real third state and a
 * two-position switch cannot express it.
 */
function AccountMenu({ account, connection, onDisconnect, onSignOut }: {
  account: { name: string; detail: string };
  connection: ConnectionState;
  onDisconnect?: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const appearanceId = useId();
  const currencyId = useId();
  const { choice, setChoice } = useTheme();
  const { currency, setCurrency, rateState } = useCurrency();
  // The panel is kept in the tree for the length of its close transition, so
  // dismissal plays instead of the menu simply blinking out.
  const disclosure = useDisclosure(open, "--dropdown-close-dur");
  /**
   * The appearance bar only exists while the panel is mounted, so the mounted
   * flag is part of the key: without it the pill would be measured once, before
   * the bar had been rendered, and stay at zero width on every open.
   */
  const appearancePill = useSlidingPill(`${choice}:${disclosure.mounted}`, '[aria-checked="true"]');
  const currencyPill = useSlidingPill(`${currency}:${disclosure.mounted}`, '[aria-checked="true"]');

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const initials = account.name.trim().slice(0, 2).toUpperCase() || "TC";

  return (
    <div className="account-menu" ref={container}>
      <button
        type="button"
        className="account-trigger"
        aria-label="Account menu"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(value => !value)}
      >
        <span className="avatar" aria-hidden="true">{initials}</span>
        <span className="account-trigger-text">
          <strong>{account.name}</strong>
          <small>{account.detail}</small>
        </span>
        <ChevronDown className="account-caret" aria-hidden="true" />
      </button>

      {disclosure.mounted && (
        <div
          className={`account-dropdown t-dropdown ${disclosure.className}`}
          /* Anchored under the trigger's right edge, so it grows out of that
             corner rather than from the top left. */
          data-origin="top-right"
          id={menuId}
          role="menu"
        >
          <div className="account-profile">
            <span className="avatar avatar-lg" aria-hidden="true">{initials}</span>
            <span className="account-profile-text">
              <strong>{account.name}</strong>
              <small>{account.detail}</small>
            </span>
          </div>

          <span className={`connection-chip tone-${connection.tone}`}>
            <StatusDot tone={connection.tone} />
            {connection.label}
          </span>

          <div className="account-section">
            <span className="account-section-label" id={appearanceId}>Appearance</span>
            <div className="appearance-switch" role="radiogroup" aria-labelledby={appearanceId} ref={appearancePill.barRef}>
              {appearancePill.pill}
              {APPEARANCE_OPTIONS.map(option => (
                <button
                  type="button"
                  key={option.value}
                  role="radio"
                  aria-checked={choice === option.value}
                  className="appearance-option"
                  onClick={() => setChoice(option.value)}
                >
                  <span aria-hidden="true">{option.icon}</span>
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="account-section">
            <span className="account-section-label" id={currencyId}>Display currency</span>
            <div className="appearance-switch currency-switch" role="radiogroup" aria-labelledby={currencyId} ref={currencyPill.barRef}>
              {currencyPill.pill}
              {CURRENCY_OPTIONS.map(option => {
                const disabled = option.value === "INR" && rateState.kind !== "ready";
                return (
                  <button
                    type="button"
                    key={option.value}
                    role="radio"
                    aria-checked={currency === option.value}
                    className="appearance-option"
                    disabled={disabled}
                    onClick={() => setCurrency(option.value)}
                  >
                    <span aria-hidden="true">{option.symbol}</span>
                    {option.label}
                  </button>
                );
              })}
            </div>
            <small className="currency-rate-note">
              {rateState.kind === "ready"
                ? `1 USD = ₹${rateState.rate.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${rateState.date ? ` · ${rateState.date}` : ""}`
                : rateState.kind === "error" ? "Rupee conversion is unavailable. Showing USD." : "Loading USD to INR rate. Showing USD meanwhile."}
            </small>
            <small className="currency-rate-note">Display conversion only. Delta amounts and orders stay in USD.</small>
          </div>

          <div className="account-section">
            {onDisconnect && (
              <button type="button" role="menuitem" onClick={() => { setOpen(false); onDisconnect(); }}>
                <KeyRound aria-hidden="true" />Disconnect Delta Exchange
              </button>
            )}
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onSignOut(); }}>
              <LogOut aria-hidden="true" />Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
