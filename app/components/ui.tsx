"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, ChevronDown, Info, MoreHorizontal, X } from "lucide-react";

/* ------------------------------------------------------------------ *
 * Shared shapes
 * ------------------------------------------------------------------ */

export type NoticeTone = "ok" | "error" | "warning";
export type Notice = { tone: NoticeTone; text: string };
export type NoticeHandler = (notice: Notice) => void;

/* ------------------------------------------------------------------ *
 * Identity
 * ------------------------------------------------------------------ */

export function Brand({ subtitle = "Delta workspace" }: { subtitle?: string }) {
  return (
    <span className="brand">
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      <span className="brand-text">
        <strong>Trade Cognition</strong>
        <small>{subtitle}</small>
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * Surfaces
 * ------------------------------------------------------------------ */

export function Panel({ className = "", children, ...rest }: React.HTMLAttributes<HTMLElement>) {
  return <section className={`panel ${className}`.trim()} {...rest}>{children}</section>;
}

/**
 * Panel header. `meta` is a short factual qualifier (record counts, refresh
 * cadence); `actions` holds controls that belong to this panel only.
 */
export function PanelHeader({ icon, title, meta, actions }: {
  icon?: ReactNode;
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="panel-header">
      <div className="panel-header-title">
        {icon && <span className="panel-icon" aria-hidden="true">{icon}</span>}
        <div>
          <h2>{title}</h2>
          {meta !== undefined && <p>{meta}</p>}
        </div>
      </div>
      {actions && <div className="panel-header-actions">{actions}</div>}
    </header>
  );
}

export function SectionHeading({ eyebrow, title, description, actions }: {
  eyebrow: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="section-heading">
      <div>
        <p className="eyebrow"><span aria-hidden="true" />{eyebrow}</p>
        <h1>{title}</h1>
        {description && <p className="section-description">{description}</p>}
      </div>
      {actions && <div className="section-heading-actions">{actions}</div>}
    </header>
  );
}

export function EmptyState({ icon, title, description, action, compact = false }: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "empty-state compact" : "empty-state"}>
      <span className="empty-state-icon" aria-hidden="true">{icon}</span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Loading placeholders shaped like the content they replace
 * ------------------------------------------------------------------ */

export function TileSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="skeleton-tiles" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div className="skeleton-tile" key={index}>
          <span className="skeleton skeleton-label" />
          <span className="skeleton skeleton-value" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 5, label }: { rows?: number; label: string }) {
  return (
    <div className="skeleton-table" role="status" aria-label={`Loading ${label}`}>
      <span className="skeleton skeleton-head" />
      {Array.from({ length: rows }, (_, index) => <span className="skeleton skeleton-row" key={index} />)}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Status
 * ------------------------------------------------------------------ */

/** Semantic families keep colour meaning stable across every surface. */
export type StatusTone = "neutral" | "positive" | "negative" | "warning" | "active";

export function StatusChip({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  return <span className={`status-chip tone-${tone}`}>{children}</span>;
}

export function StatusDot({ tone, label }: { tone: StatusTone; label?: string }) {
  return <span className={`status-dot tone-${tone}`} role={label ? "img" : undefined} aria-label={label} />;
}

/**
 * Horizontal proportion bar. `value` and `max` share a unit, so the bar always
 * answers "how much of the limit is used" rather than showing a decorative fill.
 */
export function Meter({ value, max, label, tone = "neutral" }: {
  value: number;
  max: number;
  label: string;
  tone?: StatusTone;
}) {
  const ratio = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div
      className={`meter tone-${tone}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
    >
      <i style={{ width: `${ratio}%` }} />
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Feedback
 * ------------------------------------------------------------------ */

const TOAST_ICON = { ok: <Check />, error: <AlertTriangle />, warning: <AlertTriangle /> } as const;

export function Toast({ notice, onClose }: { notice: Notice; onClose: () => void }) {
  useEffect(() => {
    if (notice.tone === "error") return;
    const timer = window.setTimeout(onClose, 7_000);
    return () => window.clearTimeout(timer);
  }, [notice, onClose]);

  return (
    <div className={`toast tone-${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
      <span className="toast-icon" aria-hidden="true">{TOAST_ICON[notice.tone]}</span>
      <p>{notice.text}</p>
      <button type="button" className="toast-close" onClick={onClose} aria-label="Dismiss notification"><X /></button>
    </div>
  );
}

export function InlineMessage({ tone, children }: { tone: NoticeTone | "info"; children: ReactNode }) {
  return (
    <p className={`inline-message tone-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <span aria-hidden="true">{tone === "ok" ? <Check /> : tone === "info" ? <Info /> : <AlertTriangle />}</span>
      <span>{children}</span>
    </p>
  );
}

/* ------------------------------------------------------------------ *
 * Modal
 * ------------------------------------------------------------------ */

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Counted page scroll lock. Modals stack — a confirmation can open over a
 * reading dialog — so the original overflow is only restored once the last
 * surface has closed, independent of the order React unmounts them in.
 */
let scrollLocks = 0;
let overflowBeforeLock = "";

function lockPageScroll() {
  if (scrollLocks === 0) {
    overflowBeforeLock = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  scrollLocks += 1;
  return () => {
    scrollLocks = Math.max(0, scrollLocks - 1);
    if (scrollLocks === 0) document.body.style.overflow = overflowBeforeLock;
  };
}

/**
 * Behaviour shared by every modal surface: the page behind stops scrolling,
 * focus moves in and is trapped, Escape dismisses, and the previously focused
 * control is restored on close.
 */
function useModalShell(onClose: () => void, initialFocus?: RefObject<HTMLElement | null>) {
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    (initialFocus?.current ?? dialog.current?.querySelector<HTMLElement>(FOCUSABLE))?.focus();
    const releaseScroll = lockPageScroll();
    return () => {
      releaseScroll();
      previouslyFocused?.focus?.();
    };
  }, [initialFocus]);

  const onKeyDown = useCallback((event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, [onClose]);

  return { dialog, onKeyDown };
}

/**
 * Confirmation dialog for actions that reach the exchange.
 *
 * Focus starts on the cancelling control, so a stray Enter keypress cannot
 * submit a live order action. Escape and the backdrop both cancel; only the
 * explicit confirm button proceeds.
 */
export function ConfirmModal({ title, description, confirm, cancel = "Keep it", tone = "danger", busy = false, onClose, onConfirm }: {
  title: string;
  description: string;
  confirm: string;
  cancel?: string;
  tone?: "danger" | "neutral";
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const cancelButton = useRef<HTMLButtonElement>(null);
  const { dialog, onKeyDown } = useModalShell(onClose, cancelButton);
  const titleId = useId();
  const descriptionId = useId();

  return (
    <div className="modal-layer" onKeyDown={onKeyDown}>
      <button type="button" className="modal-backdrop" onClick={onClose} tabIndex={-1} aria-hidden="true" />
      <div
        className={`modal tone-${tone}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        ref={dialog}
      >
        <span className="modal-icon" aria-hidden="true">{tone === "danger" ? <AlertTriangle /> : <Info />}</span>
        <h2 id={titleId}>{title}</h2>
        <p id={descriptionId}>{description}</p>
        <div className="modal-actions">
          <button type="button" className="button ghost" onClick={onClose} ref={cancelButton}>{cancel}</button>
          <button
            type="button"
            className={tone === "danger" ? "button danger" : "button primary"}
            onClick={onConfirm}
            disabled={busy}
          >{confirm}</button>
        </div>
      </div>
    </div>
  );
}

/**
 * Reading dialog for records that are too detailed for a table row.
 *
 * Unlike `ConfirmModal` this surface is wide, scrolls internally, and carries no
 * implied action, so the header keeps a single unambiguous close control.
 */
export function Dialog({ title, subtitle, aside, footer, onClose, children }: {
  title: string;
  subtitle?: ReactNode;
  aside?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const { dialog, onKeyDown } = useModalShell(onClose, closeButton);
  const titleId = useId();

  return (
    <div className="modal-layer" onKeyDown={onKeyDown}>
      <button type="button" className="modal-backdrop" onClick={onClose} tabIndex={-1} aria-hidden="true" />
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialog}>
        <header className="dialog-head">
          <div className="dialog-head-text">
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <div className="dialog-head-side">
            {aside}
            <button type="button" className="icon-button" onClick={onClose} ref={closeButton} aria-label="Close details">
              <X aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer className="dialog-foot">{footer}</footer>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Row actions
 * ------------------------------------------------------------------ */

export type RowMenuItem = {
  id: string;
  label: string;
  /** One line explaining the consequence, because these actions reach the exchange. */
  hint?: string;
  icon?: ReactNode;
  tone?: "neutral" | "danger";
  disabled?: boolean;
  onSelect: () => void;
};

const ROW_MENU_WIDTH = 264;
const ROW_MENU_ITEM_HEIGHT = 46;

/**
 * Overflow menu for a table row.
 *
 * The panel is portalled to the document and positioned from the trigger rect
 * rather than nested in the cell, because the runs table scrolls horizontally
 * and an absolutely positioned child would be clipped by that container. Any
 * scroll or resize dismisses it instead of leaving a detached panel behind.
 */
export function RowMenu({ label, items }: { label: string; items: RowMenuItem[] }) {
  const [anchor, setAnchor] = useState<{ top: number; left: number; placement: "below" | "above" } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const open = anchor !== null;

  const close = useCallback((restoreFocus = true) => {
    setAnchor(null);
    if (restoreFocus) trigger.current?.focus();
  }, []);

  function openMenu() {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const height = items.length * ROW_MENU_ITEM_HEIGHT + 12;
    const fitsBelow = window.innerHeight - rect.bottom > height + 16;
    setAnchor({
      top: fitsBelow ? rect.bottom + 6 : Math.max(8, rect.top - 6 - height),
      left: Math.max(8, Math.min(rect.right - ROW_MENU_WIDTH, window.innerWidth - ROW_MENU_WIDTH - 8)),
      placement: fitsBelow ? "below" : "above"
    });
  }

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menu.current?.contains(target) || trigger.current?.contains(target)) return;
      close(false);
    };
    const dismiss = () => close(false);
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [close, open]);

  function onMenuKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      close();
      return;
    }
    const options = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []);
    if (!options.length) return;
    const index = options.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      options[(index + 1) % options.length].focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      options[(index - 1 + options.length) % options.length].focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      options[0].focus();
    } else if (event.key === "End") {
      event.preventDefault();
      options[options.length - 1].focus();
    }
  }

  return (
    <>
      <button
        type="button"
        className={open ? "row-menu-trigger open" : "row-menu-trigger"}
        ref={trigger}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close() : openMenu())}
      >
        <MoreHorizontal aria-hidden="true" />
      </button>

      {open && createPortal(
        <div
          className={`row-menu from-${anchor.placement}`}
          id={menuId}
          role="menu"
          aria-label={label}
          ref={menu}
          style={{ top: anchor.top, left: anchor.left, width: ROW_MENU_WIDTH }}
          onKeyDown={onMenuKeyDown}
        >
          {items.map(item => (
            <button
              type="button"
              role="menuitem"
              key={item.id}
              className={item.tone === "danger" ? "danger" : undefined}
              disabled={item.disabled}
              onClick={() => { close(false); item.onSelect(); }}
            >
              {item.icon && <span className="row-menu-icon" aria-hidden="true">{item.icon}</span>}
              <span className="row-menu-text">
                <strong>{item.label}</strong>
                {item.hint && <small>{item.hint}</small>}
              </span>
            </button>
          ))}
        </div>,
        document.body
      )}
    </>
  );
}

/* ------------------------------------------------------------------ *
 * Record display
 * ------------------------------------------------------------------ */

export type DetailItem = {
  label: string;
  value: ReactNode;
  /** Identifiers and raw payloads read better in the mono stack. */
  mono?: boolean;
  /** Full-width row for long values such as errors. */
  wide?: boolean;
};

/** Label and value pairs for a single record, laid out on one shared grid. */
export function DetailList({ items }: { items: DetailItem[] }) {
  return (
    <dl className="detail-list">
      {items.map(item => (
        <div key={item.label} className={item.wide ? "wide" : undefined}>
          <dt>{item.label}</dt>
          <dd className={item.mono ? "mono" : undefined}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Titled block inside a `Dialog`, so long records stay scannable. */
export function DetailSection({ title, meta, children }: { title: string; meta?: ReactNode; children: ReactNode }) {
  return (
    <section className="detail-section">
      <header>
        <h3>{title}</h3>
        {meta !== undefined && <span>{meta}</span>}
      </header>
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Form controls
 * ------------------------------------------------------------------ */

export function Field({ label, hint, error, invalid = false, children }: {
  label: string;
  hint?: string;
  error?: string;
  invalid?: boolean;
  children: ReactNode;
}) {
  return (
    <label className={invalid || error ? "field invalid" : "field"}>
      <span className="field-label">{label}</span>
      {children}
      {error ? <small className="field-error">{error}</small> : hint ? <small className="field-hint">{hint}</small> : null}
    </label>
  );
}

export function Select({ label, value, options, onChange, invalid, hint }: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  invalid?: boolean;
  hint?: string;
}) {
  return (
    <Field label={label} invalid={invalid} hint={hint}>
      <span className="select-wrap">
        <select value={value} onChange={event => onChange(event.target.value)} aria-invalid={invalid || undefined}>
          {options.map(option => <option value={option.value} key={option.value}>{option.label}</option>)}
        </select>
        <ChevronDown aria-hidden="true" />
      </span>
    </Field>
  );
}

export function NumberField({ label, value, min = 0, max, step, suffix, onChange, invalid, hint }: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
  invalid?: boolean;
  hint?: string;
}) {
  return (
    <Field label={label} invalid={invalid} hint={hint}>
      <span className={suffix ? "input-affix" : undefined}>
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={event => onChange(Number(event.target.value))}
          aria-invalid={invalid || undefined}
        />
        {suffix && <b aria-hidden="true">{suffix}</b>}
      </span>
    </Field>
  );
}

/** Numeric input where an empty value means "this protection is off". */
export function OptionalNumberField({ label, value, onChange, invalid, hint, placeholder = "Off" }: {
  label: string;
  value?: number;
  onChange: (value?: number) => void;
  invalid?: boolean;
  hint?: string;
  placeholder?: string;
}) {
  return (
    <Field label={label} invalid={invalid} hint={hint}>
      <input
        type="number"
        min="0"
        step="any"
        value={value ?? ""}
        placeholder={placeholder}
        onChange={event => onChange(event.target.value === "" ? undefined : Number(event.target.value))}
        aria-invalid={invalid || undefined}
      />
    </Field>
  );
}

/**
 * Single-choice control exposed as a radio group rather than a tab list, so
 * assistive technology announces "1 of 3" and arrow keys move the selection.
 */
export function Segmented({ label, value, options, onChange, disabled = false, invalid = false }: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const groupId = useId();

  function move(offset: number) {
    const index = options.findIndex(option => option.value === value);
    const next = options[(index + offset + options.length) % options.length];
    if (next) onChange(next.value);
  }

  return (
    <div className={invalid ? "segmented-field invalid" : "segmented-field"}>
      <span className="field-label" id={groupId}>{label}</span>
      <div
        className="segmented"
        role="radiogroup"
        aria-labelledby={groupId}
        aria-invalid={invalid || undefined}
        style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
        onKeyDown={event => {
          if (disabled) return;
          if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); move(1); }
          if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); move(-1); }
        }}
      >
        {options.map(option => {
          const selected = option.value === value;
          return (
            <button
              type="button"
              key={option.value}
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              disabled={disabled}
              className="segmented-option"
              onClick={() => onChange(option.value)}
            >{option.label}</button>
          );
        })}
      </div>
    </div>
  );
}

export function Toggle({ label, description, checked, onChange }: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const labelId = useId();
  return (
    <div className="toggle-field">
      <span className="field-label" id={labelId}>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        className={checked ? "toggle on" : "toggle"}
        onClick={() => onChange(!checked)}
      >
        <i aria-hidden="true" />
        <span>{checked ? "On" : "Off"}</span>
      </button>
      {description && <small className="field-hint">{description}</small>}
    </div>
  );
}
