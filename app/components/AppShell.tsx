"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  Activity, BarChart3, ChevronDown, KeyRound, Layers3, LogOut, Menu, Newspaper, PieChart, X
} from "lucide-react";
import { Brand, StatusDot } from "@/app/components/ui";

export type Tab = "builder" | "market" | "news" | "dashboard" | "runs";

type NavItem = { id: Tab; label: string; hint: string; icon: ReactNode };
type NavGroup = { label: string; items: NavItem[] };

/**
 * Navigation is grouped by intent: work that changes exchange state is kept
 * apart from read-only research, so an operator never lands on an execution
 * surface while browsing analysis.
 */
const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { id: "builder", label: "Strategy builder", hint: "Configure and schedule", icon: <Layers3 /> },
      { id: "runs", label: "Run history", hint: "Scheduled and live runs", icon: <Activity /> },
      { id: "dashboard", label: "Portfolio", hint: "Balances and positions", icon: <PieChart /> }
    ]
  },
  {
    label: "Research",
    items: [
      { id: "market", label: "Market analysis", hint: "Order flow and volatility", icon: <BarChart3 /> },
      { id: "news", label: "News intelligence", hint: "Agent research outcomes", icon: <Newspaper /> }
    ]
  }
];

export type ConnectionState = {
  label: string;
  detail: string;
  tone: "active" | "warning" | "neutral";
};

export function AppShell({ tab, availableTabs, connection, account, onNavigate, onDisconnect, onSignOut, banner, children }: {
  tab: Tab;
  /** Tabs that require the trading backend are hidden while it is unreachable. */
  availableTabs: Tab[];
  connection: ConnectionState;
  account: { name: string; detail: string };
  onNavigate: (tab: Tab) => void;
  onDisconnect?: () => void;
  onSignOut: () => void;
  banner?: ReactNode;
  children: ReactNode;
}) {
  const [navOpen, setNavOpen] = useState(false);
  const groups = NAV_GROUPS
    .map(group => ({ ...group, items: group.items.filter(item => availableTabs.includes(item.id)) }))
    .filter(group => group.items.length > 0);
  const activeItem = groups.flatMap(group => group.items).find(item => item.id === tab);

  useEffect(() => {
    if (!navOpen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setNavOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  return (
    <div className="shell">
      <a className="skip-link" href="#workspace">Skip to main content</a>

      <aside className="sidebar" data-open={navOpen} aria-label="Workspace sections">
        <div className="sidebar-head">
          <Brand />
          <button type="button" className="icon-button sidebar-close" onClick={() => setNavOpen(false)} aria-label="Close navigation">
            <X />
          </button>
        </div>

        <nav className="sidebar-nav">
          {groups.map(group => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{group.label}</p>
              <ul>
                {group.items.map(item => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="nav-item"
                      aria-current={item.id === tab ? "page" : undefined}
                      onClick={() => { onNavigate(item.id); setNavOpen(false); }}
                    >
                      <span className="nav-item-icon" aria-hidden="true">{item.icon}</span>
                      <span className="nav-item-text">
                        <strong>{item.label}</strong>
                        <small>{item.hint}</small>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className={`sidebar-status tone-${connection.tone}`}>
          <StatusDot tone={connection.tone} />
          <span>
            <strong>{connection.label}</strong>
            <small>{connection.detail}</small>
          </span>
        </div>
      </aside>

      {navOpen && <button type="button" className="sidebar-scrim" onClick={() => setNavOpen(false)} aria-label="Close navigation" />}

      <div className="shell-body">
        <header className="context-bar">
          <button type="button" className="icon-button nav-toggle" onClick={() => setNavOpen(true)} aria-label="Open navigation" aria-expanded={navOpen}>
            <Menu />
          </button>
          <div className="context-title">
            <span>{activeItem ? activeItem.label : "Workspace"}</span>
            <small>{activeItem ? activeItem.hint : "Delta Exchange India"}</small>
          </div>
          <Clock />
          <AccountMenu account={account} connection={connection} onDisconnect={onDisconnect} onSignOut={onSignOut} />
        </header>

        <main className="workspace" id="workspace">
          {banner}
          {children}
        </main>
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
    <div className="context-clock">
      <time dateTime={now.toISOString()}>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
      <small>{zone} · scheduling clock</small>
    </div>
  );
}

function AccountMenu({ account, connection, onDisconnect, onSignOut }: {
  account: { name: string; detail: string };
  connection: ConnectionState;
  onDisconnect?: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const menuId = useId();

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
        aria-controls={menuId}
        onClick={() => setOpen(value => !value)}
      >
        <span className="avatar" aria-hidden="true">{initials}</span>
        <span className="account-trigger-text">
          <strong>{account.name}</strong>
          <small>{account.detail}</small>
        </span>
        <ChevronDown aria-hidden="true" />
      </button>

      {open && (
        <div className="account-dropdown" id={menuId} role="menu">
          <div className="account-dropdown-head">
            <strong>{account.name}</strong>
            <small>{account.detail}</small>
            <span className={`connection-chip tone-${connection.tone}`}>
              <StatusDot tone={connection.tone} />{connection.label}
            </span>
          </div>
          {onDisconnect && (
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onDisconnect(); }}>
              <KeyRound aria-hidden="true" />Disconnect Delta Exchange
            </button>
          )}
          <button type="button" role="menuitem" onClick={() => { setOpen(false); onSignOut(); }}>
            <LogOut aria-hidden="true" />Sign out
          </button>
        </div>
      )}
    </div>
  );
}
