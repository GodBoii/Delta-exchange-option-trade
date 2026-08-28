"use client";

import {
  useEffect, useId, useRef, useState, type ReactNode
} from "react";
import {
  Activity, BarChart3, Bot, ChevronDown, KeyRound, Layers3, LogOut, Newspaper, PieChart,
  ThemeDark, ThemeLight, ThemeSystem
} from "@/app/components/icons";
import { useTheme, type ThemeChoice } from "@/app/components/theme";
import {
  Badge, Brand, StatusDot, SwapText, Tooltip, useDisclosure, useSlidingPill
} from "@/app/components/ui";

export type Tab = "connect" | "builder" | "market" | "news" | "automation" | "dashboard" | "runs";

type NavItem = { id: Tab; label: string; hint: string; icon: ReactNode };

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
  { id: "connect", label: "Connection", hint: "Enable live execution", icon: <KeyRound />, family: "execute" },
  { id: "builder", label: "Builder", hint: "Configure and schedule", icon: <Layers3 />, family: "execute" },
  { id: "runs", label: "History", hint: "Scheduled and active strategies", icon: <Activity />, family: "execute" },
  { id: "dashboard", label: "Portfolio", hint: "Balances, positions and capital", icon: <PieChart />, family: "execute" },
  { id: "market", label: "Market", hint: "Order flow and volatility", icon: <BarChart3 />, family: "research" },
  { id: "news", label: "News", hint: "Headlines and market impact", icon: <Newspaper />, family: "research" },
  { id: "automation", label: "Automation", hint: "Agent reviews and proposals", icon: <Bot />, family: "research" }
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
  const activeItem = items.find(item => item.id === tab);
  const { barRef, pill } = useSlidingPill(`${tab}:${availableTabs.join(",")}`, '[aria-current="page"]');
  const activeRef = useRef<HTMLButtonElement>(null);

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
          <Brand subtitle={activeItem ? activeItem.hint : "Delta Exchange India"} />

          <Tooltip label={connection.detail} placement="bottom">
            <span className={`live-pill tone-${connection.tone}`}>
              <StatusDot tone={connection.tone} />
              {/* The label changes with the connection, so it swaps in place
                  rather than being replaced between frames. */}
              <span className="live-pill-text"><SwapText>{connection.label}</SwapText></span>
            </span>
          </Tooltip>

          <div className="topbar-actions">
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
  const { choice, setChoice } = useTheme();
  // The panel is kept in the tree for the length of its close transition, so
  // dismissal plays instead of the menu simply blinking out.
  const disclosure = useDisclosure(open, "--dropdown-close-dur");
  /**
   * The appearance bar only exists while the panel is mounted, so the mounted
   * flag is part of the key: without it the pill would be measured once, before
   * the bar had been rendered, and stay at zero width on every open.
   */
  const { barRef, pill } = useSlidingPill(`${choice}:${disclosure.mounted}`, '[aria-checked="true"]');

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
            <div className="appearance-switch" role="radiogroup" aria-labelledby={appearanceId} ref={barRef}>
              {pill}
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
