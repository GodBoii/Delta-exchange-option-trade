"use client";

import {
  useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode
} from "react";
import {
  Activity, BarChart3, ChevronDown, KeyRound, Layers3, LogOut, Menu, Newspaper, PieChart, X
} from "lucide-react";
import {
  Badge, Brand, IconSwap, StatusDot, SwapText, Tooltip, useDisclosure
} from "@/app/components/ui";

export type Tab = "connect" | "builder" | "market" | "news" | "dashboard" | "runs";

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
      { id: "connect", label: "Delta connection", hint: "Enable live execution", icon: <KeyRound /> },
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

/** Reading order of the sections, so a transition knows which way it travelled. */
export const TAB_ORDER: readonly Tab[] = NAV_GROUPS.flatMap(group => group.items.map(item => item.id));

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
  const [navOpen, setNavOpen] = useState(false);
  const groups = NAV_GROUPS
    .map(group => ({ ...group, items: group.items.filter(item => availableTabs.includes(item.id)) }))
    .filter(group => group.items.length > 0);
  const activeItem = groups.flatMap(group => group.items).find(item => item.id === tab);
  const navRef = useRef<HTMLElement>(null);
  const railRef = useRef<HTMLSpanElement>(null);
  const layoutKey = availableTabs.join(",");

  useEffect(() => {
    if (!navOpen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setNavOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  /**
   * One rail travels to the active section instead of a marker switching off one
   * row and on to another, so the sidebar shows where the selection came from —
   * the sliding-tabs idea, turned on its side.
   *
   * Position and height are measured from the live DOM rather than derived from
   * an index, because the rows are grouped and a group can be hidden entirely
   * while the trading backend is unreachable. The first write suspends the
   * transition, otherwise the rail would animate down from the top on mount.
   */
  useLayoutEffect(() => {
    const nav = navRef.current;
    const rail = railRef.current;
    if (!nav || !rail) return;

    const place = (animate: boolean) => {
      const active = nav.querySelector<HTMLElement>('.nav-item[aria-current="page"]');
      if (!active) {
        rail.dataset.ready = "false";
        return;
      }
      const apply = () => {
        rail.style.transform = `translateY(${active.offsetTop}px)`;
        rail.style.height = `${active.offsetHeight}px`;
      };
      if (animate) {
        apply();
      } else {
        const previous = rail.style.transition;
        rail.style.transition = "none";
        apply();
        void rail.offsetHeight;
        rail.style.transition = previous;
      }
      rail.dataset.ready = "true";
    };

    place(rail.dataset.ready === "true");
    const onResize = () => place(false);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [layoutKey, tab]);

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

        <nav className="sidebar-nav" ref={navRef}>
          <span className="nav-rail" ref={railRef} data-ready="false" aria-hidden="true" />
          {groups.map(group => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{group.label}</p>
              <ul>
                {group.items.map(item => {
                  const count = badges?.[item.id] ?? 0;
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        className="nav-item"
                        aria-current={item.id === tab ? "page" : undefined}
                        onClick={() => { onNavigate(item.id); setNavOpen(false); }}
                      >
                        <span className="nav-item-icon" aria-hidden="true">
                          {item.icon}
                          {/* Only the badge slides and pops; the row it sits on
                              never moves, so the navigation cannot jump under
                              the cursor when a run starts needing attention. */}
                          <Badge
                            count={count}
                            tone="negative"
                            label={`${count} ${item.label.toLowerCase()} ${count === 1 ? "item needs" : "items need"} attention`}
                          />
                        </span>
                        <span className="nav-item-text">
                          <strong>{item.label}</strong>
                          <small>{item.hint}</small>
                        </span>
                      </button>
                    </li>
                  );
                })}
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
          <button
            type="button"
            className="icon-button nav-toggle"
            onClick={() => setNavOpen(value => !value)}
            aria-label={navOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={navOpen}
          >
            {/* Both glyphs stay mounted in one grid cell, so the control never
                changes size as it flips and the bar cannot reflow. */}
            <IconSwap showB={navOpen} a={<Menu />} b={<X />} />
          </button>
          <div className="context-title">
            {/* The section name changes in place rather than between frames. */}
            <SwapText>{activeItem ? activeItem.label : "Workspace"}</SwapText>
            <small><SwapText>{activeItem ? activeItem.hint : "Delta Exchange India"}</SwapText></small>
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
    <Tooltip label="Entry and exit times are scheduled against this clock" placement="bottom">
      <div className="context-clock">
        <time dateTime={now.toISOString()}>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
        <small>{zone} · scheduling clock</small>
      </div>
    </Tooltip>
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
  // The panel is kept in the tree for the length of its close transition, so
  // dismissal plays instead of the menu simply blinking out.
  const disclosure = useDisclosure(open, "--dropdown-close-dur");

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
