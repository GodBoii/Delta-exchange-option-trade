"use client";

import Image from "next/image";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, ChevronDown, Info, MoreHorizontal, X } from "@/app/components/icons";
import { DrawnTick, readMs, SwapText, useSlidingPill } from "@/app/components/motion";

/* Re-exported so a surface can pull a primitive and its motion from one place
 * rather than importing the same recipe from two modules. */
export {
  AnimatedNumber, Badge, ClearableInput, DrawnTick, HoverGroup, IconSwap, LearnMoreChevron,
  MorphMenu, PageEnter, readMs, Shimmer, SpinningCounter, SuccessCheck, SwapText, TiltCard, Tooltip,
  useBurst, useClearDissolve, useDisclosure, useReducedMotion, useShake, useSlidingPill,
  useTravelDirection
} from "@/app/components/motion";

/* ------------------------------------------------------------------ *
 * Shared shapes
 * ------------------------------------------------------------------ */

export type NoticeTone = "ok" | "error" | "warning";
export type Notice = { tone: NoticeTone; text: string };
export type NoticeHandler = (notice: Notice) => void;

/* ------------------------------------------------------------------ *
 * Identity
 * ------------------------------------------------------------------ */

export function Brand({ subtitle = "Trading dashboard" }: { subtitle?: string }) {
  return (
    <span className="brand">
      <Image
        className="brand-mark"
        src="/polycognition-mark.png"
        width={32}
        height={32}
        alt=""
        aria-hidden="true"
      />
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

/**
 * The header of a workspace surface.
 *
 * Deliberately small. It used to open with a tracked-out eyebrow above a 38px
 * display heading, which is a landing-page composition: on an operational
 * screen that pushed the first row of figures below the fold and spent the most
 * prominent type in the interface restating the destination the operator had
 * just clicked. Title, one line of orientation, and the controls for this
 * surface, on one row.
 */
export function SectionHeading({ title, description, actions }: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="section-heading">
      <div className="section-heading-text">
        <h1>{title}</h1>
        {description && <p className="section-description">{description}</p>}
      </div>
      {actions && <div className="section-heading-actions">{actions}</div>}
    </header>
  );
}

/**
 * Empty state.
 *
 * Stacked copy entering with rhythm, so it uses the staggered text reveal: the
 * eye lands on the icon, then the heading, then the explanation, then the way
 * out. The whole stagger stays under 300ms so the action never feels late.
 */
export function EmptyState({ icon, title, description, action, compact = false }: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <div
      className={`empty-state t-stagger${compact ? " compact" : ""}${shown ? " is-shown" : ""}`}
    >
      <span className="empty-state-icon t-stagger-line t-stagger-line--1" aria-hidden="true">{icon}</span>
      <h3 className="t-stagger-line t-stagger-line--2">{title}</h3>
      <p className="t-stagger-line t-stagger-line--3">{description}</p>
      {action && <span className="t-stagger-line t-stagger-line--4">{action}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Loading placeholders shaped like the content they replace
 * ------------------------------------------------------------------ */

/**
 * Placeholders keep the shimmer sweep as their in-progress signal — that is the
 * documented use for shimmer, and it reads better than a pulse on bars that are
 * already a gradient.
 *
 * They are deliberately *not* the absolute `t-skel-skeleton` layer: these render
 * in flow and are unmounted when the record lands, so there is no second layer
 * on the same coordinates to cross-fade against. The reveal is carried by the
 * content instead, via `Revealed`, on the same clock, blur and easing.
 */
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

/**
 * The content side of that same reveal, for records whose placeholder unmounts
 * rather than sharing coordinates with them.
 */
export function Revealed({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={className ? `t-reveal ${className}` : "t-reveal"}>{children}</div>;
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

const TOAST_AUTO_DISMISS_MS = 7_000;

/**
 * Transient confirmation.
 *
 * Arrival and dismissal are asymmetric by design: it rises in over 350ms so the
 * message is noticed, and leaves in 250ms so it gets out of the way. Because
 * React would otherwise unmount it the instant the notice clears, the toast owns
 * its own exit and only tells the parent once the animation has finished.
 *
 * Errors still never auto-dismiss — a failed exchange action has to be read.
 */
export function Toast({ notice, onClose }: { notice: Notice; onClose: () => void }) {
  const [open, setOpen] = useState(false);
  const closing = useRef(false);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const frame = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const dismiss = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    setOpen(false);
    window.setTimeout(() => close.current(), readMs("--toast-close", 250));
  }, []);

  useEffect(() => {
    if (notice.tone === "error") return;
    const timer = window.setTimeout(dismiss, TOAST_AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [dismiss, notice.tone]);

  return (
    <div
      className={`toast t-toast tone-${notice.tone}${open ? " is-open" : ""}`}
      role={notice.tone === "error" ? "alert" : "status"}
    >
      <span className="toast-icon" aria-hidden="true">{TOAST_ICON[notice.tone]}</span>
      <p>{notice.text}</p>
      <button type="button" className="toast-close" onClick={dismiss} aria-label="Dismiss notification"><X /></button>
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
 * Owns the surface's own entrance and exit.
 *
 * A modal is rendered conditionally by its parent, so without this the closing
 * half of the transition would never be seen — the element would be gone from
 * the tree before it could animate. The surface starts in the recipe's pre-open
 * rest state, flips to `is-open` on the next frame so the transition has two
 * frames to interpolate between, and on dismissal holds itself in `is-closing`
 * for exactly the close duration before telling the parent it is finished.
 *
 * `requestClose` is idempotent: a backdrop click during the Escape close cannot
 * start a second exit.
 */
function useModalExit(onClose: () => void) {
  const [state, setState] = useState<"pre" | "open" | "closing">("pre");
  const closing = useRef(false);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const frame = requestAnimationFrame(() => setState(current => (current === "pre" ? "open" : current)));
    return () => cancelAnimationFrame(frame);
  }, []);

  const requestClose = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    setState("closing");
    window.setTimeout(() => close.current(), readMs("--modal-close-dur", 150));
  }, []);

  const className = state === "open" ? " is-open" : state === "closing" ? " is-closing" : "";
  return { className, requestClose };
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
  const { className, requestClose } = useModalExit(onClose);
  const { dialog, onKeyDown } = useModalShell(requestClose, cancelButton);
  const titleId = useId();
  const descriptionId = useId();

  return (
    <div className="modal-layer" onKeyDown={onKeyDown}>
      <button
        type="button"
        className={`modal-backdrop t-modal-scrim${className}`}
        onClick={requestClose}
        tabIndex={-1}
        aria-hidden="true"
      />
      <div
        className={`modal t-modal tone-${tone}${className}`}
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
          <button type="button" className="button ghost" onClick={requestClose} ref={cancelButton}>{cancel}</button>
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
export function Dialog({ title, subtitle, aside, footer, size = "default", onClose, children }: {
  title: string;
  subtitle?: ReactNode;
  aside?: ReactNode;
  footer?: ReactNode;
  size?: "default" | "compact";
  onClose: () => void;
  children: ReactNode;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const { className, requestClose } = useModalExit(onClose);
  const { dialog, onKeyDown } = useModalShell(requestClose, closeButton);
  const titleId = useId();

  return (
    <div className="modal-layer" onKeyDown={onKeyDown}>
      <button
        type="button"
        className={`modal-backdrop t-modal-scrim${className}`}
        onClick={requestClose}
        tabIndex={-1}
        aria-hidden="true"
      />
      <div
        className={`dialog${size === "compact" ? " dialog-compact" : ""} t-modal${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialog}
      >
        <header className="dialog-head">
          <div className="dialog-head-text">
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <div className="dialog-head-side">
            {aside}
            <button type="button" className="icon-button" onClick={requestClose} ref={closeButton} aria-label="Close details">
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
  const [phase, setPhase] = useState<"pre" | "open" | "closing">("pre");
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const open = anchor !== null && phase !== "closing";

  /**
   * The panel is held in `is-closing` for the close duration rather than being
   * unmounted immediately, so dismissal plays its own quicker transition instead
   * of the menu simply disappearing.
   */
  const close = useCallback((restoreFocus = true) => {
    setPhase(current => (current === "closing" ? current : "closing"));
    if (restoreFocus) trigger.current?.focus();
    window.setTimeout(() => setAnchor(null), readMs("--dropdown-close-dur", 150));
  }, []);

  function openMenu() {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const height = items.length * ROW_MENU_ITEM_HEIGHT + 12;
    const fitsBelow = window.innerHeight - rect.bottom > height + 16;
    setPhase("pre");
    setAnchor({
      top: fitsBelow ? rect.bottom + 6 : Math.max(8, rect.top - 6 - height),
      left: Math.max(8, Math.min(rect.right - ROW_MENU_WIDTH, window.innerWidth - ROW_MENU_WIDTH - 8)),
      placement: fitsBelow ? "below" : "above"
    });
  }

  // Flip to the open state one frame after mounting, so the growth transition
  // has a pre-open state to interpolate from.
  useEffect(() => {
    if (anchor === null) return;
    const frame = requestAnimationFrame(() => setPhase(current => (current === "pre" ? "open" : current)));
    return () => cancelAnimationFrame(frame);
  }, [anchor]);

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

      {anchor !== null && createPortal(
        <div
          className={`row-menu t-dropdown from-${anchor.placement}${
            phase === "open" ? " is-open" : phase === "closing" ? " is-closing" : ""
          }`}
          /* The panel's right edge is aligned to the trigger's, so it grows out
             of the corner it is actually anchored to rather than the top left. */
          data-origin={anchor.placement === "below" ? "top-right" : "bottom-right"}
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

/**
 * Label, control, and the one line of help or error text beneath it.
 *
 * A native input gets a real wrapping `<label>`, which is what makes the label
 * text part of the control's hit area. When the caller passes `labelId` it is
 * wrapping a composite control that names itself with `aria-labelledby`, so the
 * wrapper becomes a plain element: `<label>` forwards clicks to any labelable
 * descendant, and a forwarded click on a listbox trigger would immediately
 * re-toggle the panel the first click just opened.
 */
export function Field({ label, hint, error, invalid = false, labelId, children }: {
  label: string;
  hint?: string;
  error?: string;
  invalid?: boolean;
  labelId?: string;
  children: ReactNode;
}) {
  const className = invalid || error ? "field invalid" : "field";
  const body = (
    <>
      <span className="field-label" id={labelId}>{label}</span>
      {children}
      {error ? <small className="field-error">{error}</small> : hint ? <small className="field-hint">{hint}</small> : null}
    </>
  );

  if (labelId) return <div className={className}>{body}</div>;
  return <label className={className}>{body}</label>;
}

export type SelectOption = {
  value: string;
  label: string;
  /** One line on what choosing this does, shown under the label. */
  hint?: string;
  /** Options carrying the same group name are listed under one heading. */
  group?: string;
  disabled?: boolean;
};

/**
 * Runs of options that share a group name, in the order they were given.
 *
 * Grouping is derived rather than required as nested input, so a caller can
 * build one flat list and the ungrouped case needs no special shape.
 */
function groupOptions(options: SelectOption[]) {
  const runs: { group?: string; options: SelectOption[] }[] = [];
  for (const option of options) {
    const last = runs[runs.length - 1];
    if (last && last.group === option.group) last.options.push(option);
    else runs.push({ group: option.group, options: [option] });
  }
  return runs;
}

const LISTBOX_MAX_HEIGHT = 288;
const LISTBOX_GAP = 6;
const LISTBOX_VIEWPORT_MARGIN = 12;
const TYPEAHEAD_RESET_MS = 700;

/**
 * Single-choice control.
 *
 * A native `select` is replaced here because its popup is drawn by the operating
 * system: it cannot show the per-option explanation these settings need, it
 * ignores every token in this stylesheet, and on a dark interface it punches a
 * light rectangle through the page. This is the ARIA listbox pattern instead —
 * a labelled trigger plus an owned `listbox`, which is the one case the
 * component guidance names for reaching past native HTML.
 *
 * The panel is portalled to the document and positioned from the trigger rect
 * rather than nested in the field, because several of these sit inside panels
 * that scroll or clip, and an absolutely positioned child would be cut off.
 * It flips above the trigger when there is no room below.
 */
export function Select({ label, value, options, onChange, invalid, hint, disabled = false }: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  invalid?: boolean;
  hint?: string;
  disabled?: boolean;
}) {
  const [rect, setRect] = useState<{ top: number; left: number; width: number; origin: "top" | "bottom" } | null>(null);
  const [phase, setPhase] = useState<"pre" | "open" | "closing">("pre");
  const [activeValue, setActiveValue] = useState(value);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const typeahead = useRef({ query: "", at: 0 });
  const listId = useId();
  const labelId = useId();
  const open = rect !== null && phase !== "closing";

  const selected = options.find(option => option.value === value);
  const enabled = options.filter(option => !option.disabled);

  /**
   * Held in `is-closing` for the close duration rather than unmounted at once,
   * so dismissal plays its own quicker transition instead of the panel blinking
   * out of existence.
   */
  const close = useCallback((restoreFocus = true) => {
    setPhase("closing");
    if (restoreFocus) trigger.current?.focus();
    window.setTimeout(() => setRect(null), readMs("--dropdown-close-dur", 150));
  }, []);

  const openPanel = useCallback(() => {
    const bounds = trigger.current?.getBoundingClientRect();
    if (!bounds) return;
    const wanted = Math.min(LISTBOX_MAX_HEIGHT, options.length * 44 + 12);
    const fitsBelow = window.innerHeight - bounds.bottom > wanted + LISTBOX_VIEWPORT_MARGIN;
    setPhase("pre");
    setActiveValue(value);
    setRect({
      top: fitsBelow ? bounds.bottom + LISTBOX_GAP : Math.max(LISTBOX_VIEWPORT_MARGIN, bounds.top - LISTBOX_GAP - wanted),
      left: bounds.left,
      width: bounds.width,
      // The panel grows out of the edge it is anchored to, not the top left.
      origin: fitsBelow ? "top" : "bottom"
    });
  }, [options.length, value]);

  // Flip to the open state one frame after mounting, so the growth transition
  // has a pre-open state to interpolate from.
  useEffect(() => {
    if (rect === null) return;
    const frame = requestAnimationFrame(() => setPhase(current => (current === "pre" ? "open" : current)));
    return () => cancelAnimationFrame(frame);
  }, [rect]);

  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [activeValue, open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return;
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

  function commit(next: string) {
    if (next !== value) onChange(next);
    close();
  }

  function step(offset: number) {
    if (!enabled.length) return;
    const index = enabled.findIndex(option => option.value === activeValue);
    const next = enabled[Math.min(enabled.length - 1, Math.max(0, (index === -1 ? 0 : index) + offset))];
    if (next) setActiveValue(next.value);
  }

  /**
   * Typeahead. Keystrokes inside a short window build one query, so "half"
   * lands on the 50% rule rather than cycling through four options starting
   * with H.
   */
  function jumpTo(character: string) {
    const now = performance.now();
    typeahead.current.query = now - typeahead.current.at > TYPEAHEAD_RESET_MS
      ? character
      : typeahead.current.query + character;
    typeahead.current.at = now;
    const query = typeahead.current.query.toLowerCase();
    const match = enabled.find(option => option.label.toLowerCase().startsWith(query));
    if (match) setActiveValue(match.value);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (disabled) return;

    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPanel();
      }
      return;
    }

    switch (event.key) {
      case "Escape":
      case "Tab":
        event.preventDefault();
        close();
        return;
      case "ArrowDown":
        event.preventDefault();
        step(1);
        return;
      case "ArrowUp":
        event.preventDefault();
        step(-1);
        return;
      case "Home":
        event.preventDefault();
        if (enabled[0]) setActiveValue(enabled[0].value);
        return;
      case "End":
        event.preventDefault();
        if (enabled.length) setActiveValue(enabled[enabled.length - 1].value);
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        commit(activeValue);
        return;
      default:
        if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) jumpTo(event.key);
    }
  }

  return (
    <Field label={label} invalid={invalid} hint={hint} labelId={labelId}>
      <button
        type="button"
        ref={trigger}
        className={open ? "listbox-trigger is-open" : "listbox-trigger"}
        disabled={disabled}
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listId : undefined}
        aria-labelledby={labelId}
        aria-invalid={invalid || undefined}
        onClick={() => (open ? close() : openPanel())}
        onKeyDown={onKeyDown}
      >
        <span className="listbox-value">{selected ? selected.label : "Select an option"}</span>
        <ChevronDown className="listbox-caret" aria-hidden="true" />
      </button>

      {rect !== null && createPortal(
        <div
          className={`listbox t-dropdown${phase === "open" ? " is-open" : phase === "closing" ? " is-closing" : ""}`}
          data-origin={rect.origin === "top" ? "top-left" : "bottom-left"}
          id={listId}
          role="listbox"
          aria-labelledby={labelId}
          aria-activedescendant={`${listId}-${activeValue}`}
          ref={panel}
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            maxHeight: LISTBOX_MAX_HEIGHT
          }}
        >
          {groupOptions(options).map((run, runIndex) => (
            <div className="listbox-group" role="group" aria-label={run.group} key={run.group ?? `run-${runIndex}`}>
              {run.group && <p className="listbox-group-label">{run.group}</p>}
              {run.options.map(option => {
                const isSelected = option.value === value;
                return (
                  <div
                    key={option.value}
                    id={`${listId}-${option.value}`}
                    role="option"
                    aria-selected={isSelected}
                    aria-disabled={option.disabled || undefined}
                    data-active={option.value === activeValue}
                    className="listbox-option"
                    onPointerEnter={() => { if (!option.disabled) setActiveValue(option.value); }}
                    onClick={() => { if (!option.disabled) commit(option.value); }}
                  >
                    <span className="listbox-option-text">
                      <strong>{option.label}</strong>
                      {option.hint && <small>{option.hint}</small>}
                    </span>
                    {/* The tick draws itself as the row becomes the chosen one, so
                        the selection is carried by motion as well as by colour. */}
                    <span className="listbox-tick t-check" aria-hidden="true" aria-checked={isSelected}>
                      <DrawnTick />
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>,
        document.body
      )}
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
 *
 * The selection is one pill that travels between the options rather than a
 * background switching off one button and on to another, so the control shows
 * which way the choice moved and not only where it ended up. On first paint the
 * pill is positioned with its transition suspended, otherwise it would slide in
 * from zero width at the left edge every time the control mounts.
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
  const { barRef, pill } = useSlidingPill(value, '[aria-checked="true"]');

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
        ref={barRef}
        aria-labelledby={groupId}
        aria-invalid={invalid || undefined}
        style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
        onKeyDown={event => {
          if (disabled) return;
          if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); move(1); }
          if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); move(-1); }
        }}
      >
        {pill}
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

/**
 * On/off switch.
 *
 * The thumb travels with a double-bounce overshoot, so flipping it reads as a
 * physical throw rather than a value being assigned. `is-init` is only added
 * once the operator has actually interacted: without it, every switch on the
 * page would play its return bounce on first paint.
 *
 * The label also swaps in place rather than being replaced between frames.
 */
export function Toggle({ label, description, checked, onChange }: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const labelId = useId();
  const [interacted, setInteracted] = useState(false);

  return (
    <div className="toggle-field">
      <span className="field-label" id={labelId}>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        data-on={checked}
        className={`toggle t-toggle${checked ? " on" : ""}${interacted ? " is-init" : ""}`}
        onClick={() => { setInteracted(true); onChange(!checked); }}
      >
        <i aria-hidden="true"><span className="t-toggle-thumb" /></i>
        <SwapText>{checked ? "On" : "Off"}</SwapText>
      </button>
      {description && <small className="field-hint">{description}</small>}
    </div>
  );
}
