"use client";

/**
 * Motion primitives.
 *
 * Each export here is one recipe from the motion catalogue installed in section
 * 3 of `globals.css`, wrapped in the smallest React surface that can drive it.
 * The CSS owns every duration, distance and curve; this file only orchestrates
 * the class and attribute changes the recipes document.
 *
 * Two conventions run through the whole file:
 *
 *   - Timings are read back out of the stylesheet with `getComputedStyle`
 *     rather than duplicated as JavaScript constants. Retuning a token in
 *     `:root` therefore retunes the orchestration with it, and the two can
 *     never drift apart.
 *   - Anything that replays a keyframe animation removes the class, forces a
 *     reflow (`void node.offsetWidth`), then re-adds it. Without the reflow the
 *     browser coalesces both changes into one frame and the animation does not
 *     restart.
 */

import {
  useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type ReactNode, type RefObject
} from "react";
import { X } from "lucide-react";

/* ------------------------------------------------------------------ *
 * Shared helpers
 * ------------------------------------------------------------------ */

/** Reads a duration token in milliseconds, so JS and CSS share one source. */
export function readMs(name: string, fallback: number) {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return fallback;
  const value = parseFloat(raw);
  if (!Number.isFinite(value)) return fallback;
  return raw.endsWith("ms") ? value : value * 1000;
}

/** Reads a unitless or px token as a number. */
function readNumber(name: string, fallback: number) {
  if (typeof window === "undefined") return fallback;
  const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

/** Reads an easing token, falling back to the documented default. */
function readEase(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Tracks the reduced-motion preference.
 *
 * The CSS guards already stop the animations, but pointer-driven effects also
 * need to stop *writing* transforms, otherwise a card would sit permanently
 * tilted at whatever angle the cursor left it at.
 */
export function useReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia(REDUCED_MOTION_QUERY);
    setReduced(query.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

export type DisclosureState = "open" | "closing";

/**
 * Keeps a surface mounted for the length of its close transition.
 *
 * React would otherwise unmount the element the moment its state flips, and the
 * closing half of every dropdown and modal recipe would never be seen. The
 * close duration is read from the same token the CSS uses.
 */
export function useDisclosure(open: boolean, closeDurationVar: string, fallback = 150) {
  const [mounted, setMounted] = useState(open);
  const [state, setState] = useState<DisclosureState>(open ? "open" : "closing");

  useEffect(() => {
    if (open) {
      setMounted(true);
      // Paint the pre-open state once, then flip, so the transition has two
      // frames to interpolate between instead of starting from its end state.
      const frame = requestAnimationFrame(() => setState("open"));
      return () => cancelAnimationFrame(frame);
    }
    if (!mounted) return;
    setState("closing");
    const timer = window.setTimeout(() => setMounted(false), readMs(closeDurationVar, fallback));
    return () => window.clearTimeout(timer);
  }, [closeDurationVar, fallback, mounted, open]);

  return { mounted, state, className: state === "open" ? "is-open" : "is-closing" };
}

/* ------------------------------------------------------------------ *
 * 02 · Number pop-in
 * ------------------------------------------------------------------ */

/**
 * A figure that re-enters when it changes.
 *
 * Balances and prices stream in on a poll, and a number that swaps silently
 * between frames is easy to miss. Each glyph rides in with a blurred slide, the
 * last two staggered, so the eye is drawn to the fact that it moved without the
 * value ever being unreadable.
 *
 * The glyph spans are hidden from assistive technology and the whole value is
 * announced once, so a screen reader reads "1,204.5" rather than seven
 * characters in a row.
 */
export function AnimatedNumber({ value, minReplayMs = 0, className }: {
  value: string;
  /**
   * Shortest gap between full replays. A websocket price can tick several times
   * a second, and restarting a 500ms animation on every frame reads as jitter
   * and burns compositor time for nothing.
   *
   * Inside the guard window the figure still animates, just not as a whole:
   * each glyph is keyed on its own value, so only the characters that actually
   * changed are remounted and only those re-enter. That is the better reading of
   * a live price anyway — the moving digits are the news.
   */
  minReplayMs?: number;
  className?: string;
}) {
  const group = useRef<HTMLSpanElement>(null);
  const previous = useRef<string | null>(null);
  const lastReplay = useRef(0);
  const characters = useMemo(() => Array.from(value), [value]);

  useEffect(() => {
    const node = group.current;
    if (!node) return;
    if (previous.current === value) return;

    // No entrance on first paint: the figure was not updated, it simply arrived.
    // The class is still applied, so later per-glyph remounts animate on their own.
    if (previous.current === null) {
      previous.current = value;
      node.classList.add("is-animating");
      return;
    }
    previous.current = value;

    const now = performance.now();
    if (now - lastReplay.current < minReplayMs) return;
    lastReplay.current = now;

    node.classList.remove("is-animating");
    void node.offsetWidth;
    node.classList.add("is-animating");
  }, [minReplayMs, value]);

  return (
    <span className={className ? `t-digit-group ${className}` : "t-digit-group"} ref={group}>
      <span className="visually-hidden">{value}</span>
      {characters.map((character, index) => (
        <span
          className="t-digit"
          key={`${index}-${character}`}
          aria-hidden="true"
          data-stagger={
            index === characters.length - 2 ? "1" : index === characters.length - 1 ? "2" : undefined
          }
        >
          {character === " " ? "\u00a0" : character}
        </span>
      ))}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 26 · Spinning counter
 * ------------------------------------------------------------------ */

const REEL_SPINS = 2;

/**
 * A count that rolls like a reel.
 *
 * Reserved for changes the operator asked for — pressing Refresh — rather than
 * the background poll, which uses `AnimatedNumber`. A slot-machine roll marks
 * an event; firing one every thirty seconds unprompted would only be noise.
 *
 * The streak is a vertical-only SVG blur: a CSS `blur()` would smear sideways
 * as well and turn the digits to mush.
 */
export function SpinningCounter({ value, animateOnMount = false, className }: {
  value: string;
  /**
   * Roll the reels the first time the figure is painted. Use it where the arrival
   * of the data is itself the event — a portfolio loading for the first time —
   * rather than leaving the first render silent.
   */
  animateOnMount?: boolean;
  className?: string;
}) {
  const filterId = useId().replace(/[^a-zA-Z0-9-]/g, "");
  const root = useRef<HTMLSpanElement>(null);
  const previous = useRef<string | null>(animateOnMount ? "" : null);
  const reduced = useReducedMotion();
  const cells = useMemo(() => Array.from({ length: (REEL_SPINS + 1) * 10 }, (_, i) => i % 10), []);
  const characters = useMemo(() => Array.from(value), [value]);

  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const first = previous.current === null;
    const unchanged = previous.current === value;
    previous.current = value;

    const columns = Array.from(node.querySelectorAll<HTMLElement>(".t-reel-strip"));
    const cell = readNumber("--reel-cell", 30);
    const duration = readMs("--reel-dur", 1400);
    const stagger = readMs("--reel-stagger", 90);
    const ease = readEase("--reel-ease", "cubic-bezier(0.16, 1, 0.3, 1)");
    const peakBlur = readNumber("--reel-spin-blur", 3);
    const blurNode = node.querySelector<SVGFEGaussianBlurElement>("feGaussianBlur");

    const settle = (strip: HTMLElement) => {
      const digit = Number(strip.dataset.digit ?? 0);
      strip.style.transition = "none";
      strip.style.transform = `translateY(${-digit * cell}px)`;
    };

    if (reduced || first || unchanged) {
      columns.forEach(settle);
      blurNode?.setAttribute("stdDeviation", "0 0");
      return;
    }

    let raf = 0;
    columns.forEach((strip, index) => {
      const digit = Number(strip.dataset.digit ?? 0);
      strip.style.transition = "none";
      strip.style.transform = "translateY(0px)";
      void strip.offsetHeight;
      strip.style.transition = `transform ${duration}ms ${ease} ${index * stagger}ms`;
      strip.style.transform = `translateY(${-(REEL_SPINS * 10 + digit) * cell}px)`;
    });

    // Decay the streak over the roll so the digits sharpen as they land.
    const total = duration + Math.max(0, columns.length - 1) * stagger;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / Math.max(1, total));
      blurNode?.setAttribute("stdDeviation", `0 ${(peakBlur * (1 - progress)).toFixed(2)}`);
      if (progress < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [reduced, value]);

  return (
    <span className={className ? `t-reel ${className}` : "t-reel"} ref={root}>
      <span className="visually-hidden">{value}</span>
      <svg width="0" height="0" aria-hidden="true" focusable="false" style={{ position: "absolute" }}>
        <filter id={filterId}><feGaussianBlur stdDeviation="0 0" /></filter>
      </svg>
      {characters.map((character, index) => {
        const digit = Number(character);
        if (!Number.isInteger(digit) || character.trim() === "") {
          return <span className="t-reel-static" key={`${index}-${character}`} aria-hidden="true">{character}</span>;
        }
        return (
          <span className="t-reel-col" key={`${index}-col`} aria-hidden="true">
            <span className="t-reel-strip" data-digit={digit} style={{ filter: `url(#${filterId})` }}>
              {cells.map((cellDigit, cellIndex) => (
                <span className="t-reel-digit" key={cellIndex}>{cellDigit}</span>
              ))}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 04 · Text states swap
 * ------------------------------------------------------------------ */

/**
 * A label that changes in place.
 *
 * Used for status copy that reports one thing becoming another — "Saving" to
 * "Saved", one workspace section to the next. The old text leaves upward with
 * blur and the new text arrives from below, which reads as a single value
 * changing rather than two unrelated strings.
 *
 * Symmetric on purpose: the same 150ms in both directions, because there is no
 * open and close here, only a reversible swap.
 */
export function SwapText({ children, className }: { children: string; className?: string }) {
  const node = useRef<HTMLSpanElement>(null);
  const [text, setText] = useState(children);
  const pending = useRef(children);

  useEffect(() => {
    if (pending.current === children) return;
    pending.current = children;
    const element = node.current;
    if (!element) {
      setText(children);
      return;
    }

    element.classList.add("is-exit");
    const timer = window.setTimeout(() => {
      setText(children);
      element.classList.remove("is-exit");
      element.classList.add("is-enter-start");
      void element.offsetHeight;
      element.classList.remove("is-enter-start");
    }, readMs("--text-swap-dur", 150));

    return () => window.clearTimeout(timer);
  }, [children]);

  return (
    <span className={className ? `t-text-swap ${className}` : "t-text-swap"} ref={node}>
      {text}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 15 · Shimmer text
 * ------------------------------------------------------------------ */

/**
 * In-progress copy that stays alive without a spinner.
 *
 * Pure CSS. The visible string is duplicated into `data-text` so the gradient
 * can be clipped to the same glyphs, which is why this takes a plain string
 * rather than children.
 */
export function Shimmer({ children, className }: { children: string; className?: string }) {
  return (
    <span className={className ? `t-shimmer ${className}` : "t-shimmer"} data-text={children}>
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 17 · Tooltip
 * ------------------------------------------------------------------ */

/**
 * A hover and focus hint.
 *
 * Entrance waits 80ms so a cursor crossing the control does not trigger it;
 * dismissal is immediate, because the delay belongs only to the hover rule. The
 * wrapper is the hover target, so the pointer can drift onto the bubble without
 * the tooltip flickering.
 *
 * The label is wired with `aria-describedby`, so it is real help text rather
 * than a visual-only affordance.
 */
/**
 * The bubble is deliberately hidden from assistive technology.
 *
 * It wraps an arbitrary child, so it cannot put `aria-describedby` on the
 * element that actually takes focus, and a description hung off the wrapper
 * would not be announced. Every trigger it is used on already carries its own
 * accessible name or visible label, so the tooltip is visual reinforcement and
 * announcing it again would only duplicate what is already read out.
 *
 * The reveal is driven by `:focus-within` rather than `:focus-visible` on the
 * wrapper, so a keyboard user tabbing to the control inside still sees it.
 */
export function Tooltip({ label, placement = "top", children }: {
  label: string;
  /** `bottom` for triggers pinned near the top of the viewport. */
  placement?: "top" | "bottom";
  children: ReactNode;
}) {
  return (
    <span className="t-tt-wrap">
      <span className="t-tt-trigger">{children}</span>
      <span className="t-tt" data-placement={placement} aria-hidden="true">{label}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 09 · Icon swap
 * ------------------------------------------------------------------ */

/**
 * Two icons sharing one slot.
 *
 * Both stay mounted and stacked in the same grid cell, so the control never
 * changes size as the state flips and the surrounding layout cannot shift.
 */
export function IconSwap({ showB, a, b, className }: {
  showB: boolean;
  a: ReactNode;
  b: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={className ? `t-icon-swap ${className}` : "t-icon-swap"}
      data-state={showB ? "b" : "a"}
      aria-hidden="true"
    >
      <span className="t-icon" data-icon="a">{a}</span>
      <span className="t-icon" data-icon="b">{b}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 24 · Learn more hover
 * ------------------------------------------------------------------ */

/**
 * A trailing chevron that opens into an arrow on hover.
 *
 * Decorative only — keyboard focus and touch rest in the neutral state, so the
 * motion never carries information on its own.
 */
export function LearnMoreChevron() {
  return (
    <span className="t-learn-chevron" aria-hidden="true">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
        <path className="t-learn-arm t-learn-arm-top" d="M6 4L10 8" />
        <path className="t-learn-arm t-learn-arm-bot" d="M10 8L6 12" />
      </svg>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 10 · Success check
 * ------------------------------------------------------------------ */

const CHECK_PATH = "M5 12.5L9.5 17L19 7.5";

/**
 * Confirmation that a live action completed.
 *
 * Fade, rotate upright, settle with a bob, and draw the stroke. Reserved for
 * moments that genuinely landed on the exchange — a connection verified, a run
 * scheduled — so the animation marks something that was earned.
 *
 * `stroke-dasharray` is measured from the real path at mount rather than
 * hardcoded: too short and the tick pre-reveals, too long and it appears to
 * draw past its own end.
 */
export function SuccessCheck({ shown, label, size = 44 }: {
  shown: boolean;
  label?: string;
  size?: number;
}) {
  const wrapper = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const node = wrapper.current;
    const path = node?.querySelector<SVGPathElement>("svg path");
    if (!node || !path) return;
    const length = Math.ceil(path.getTotalLength()) + 1;
    node.style.setProperty("--check-len", String(length));
    if (!shown) {
      node.setAttribute("data-state", "out");
      return;
    }
    node.setAttribute("data-state", "out");
    void node.offsetWidth;
    node.setAttribute("data-state", "in");
  }, [shown]);

  return (
    <span
      className="t-success-check"
      ref={wrapper}
      data-state="out"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {/* Sized inline: the stylesheet gives every `svg` a 16px default, which
          would otherwise beat the width and height attributes. */}
      <svg viewBox="0 0 24 24" style={{ width: size, height: size }} fill="none">
        <path
          d={CHECK_PATH}
          stroke="currentColor"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 25 · Checkbox check
 * ------------------------------------------------------------------ */

const TICK_PATH = "M1 5.52L3.92 9.17L9.17 1";

/**
 * A tick that draws itself when a requirement passes.
 *
 * Used read-only, for the builder's pre-flight checklist: the box fills, then
 * the stroke draws, so the operator sees the instant a requirement was
 * satisfied rather than discovering it on submit. Animating the offset (rather
 * than swapping it) means a requirement that lapses reverses cleanly.
 */
export function DrawnTick() {
  const host = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const path = host.current?.querySelector("path");
    if (!path) return;
    const length = Math.ceil(path.getTotalLength()) + 1;
    path.style.setProperty("--check-len", String(length));
    (path.parentElement as HTMLElement | null)?.style.setProperty("--check-len", String(length));
  }, []);

  return (
    <svg ref={host} viewBox="0 0 10.1668 10.1668" aria-hidden="true" focusable="false">
      <path d={TICK_PATH} />
    </svg>
  );
}

/* ------------------------------------------------------------------ *
 * 12 · Error state shake
 * ------------------------------------------------------------------ */

/**
 * Replayable validation feedback.
 *
 * `.is-error` (the treatment) and `.is-shaking` (the percussive hint) are kept
 * separate so submitting the same invalid value twice shakes twice, without the
 * error styling flickering off and on in between.
 *
 * Returns a ref for the element that owns the visible border, and a `shake()`
 * to call when a submission is rejected.
 */
export function useShake<T extends HTMLElement>() {
  const target = useRef<T>(null);

  const shake = useCallback(() => {
    const node = target.current;
    if (!node) return;
    node.classList.remove("is-shaking");
    void node.offsetWidth;
    node.classList.add("is-shaking");
    const total = readMs("--shake-dur-a", 80) * 2 + readMs("--shake-dur-b", 60) * 2;
    window.setTimeout(() => node.classList.remove("is-shaking"), total + 20);
  }, []);

  return { target, shake };
}

/* ------------------------------------------------------------------ *
 * 16 · Tabs sliding
 * ------------------------------------------------------------------ */

/**
 * A single highlight that travels between options.
 *
 * The active control's measured `offsetLeft` and `offsetWidth` are written onto
 * the pill; the CSS owns the tween. On first paint and on resize the same values
 * are written with the transition suspended, otherwise the pill would animate in
 * from zero width at the left edge every time the component mounts.
 *
 * `selector` finds the active control inside the bar, so a caller can key off
 * whatever attribute it already exposes to assistive technology.
 */
export function useSlidingPill(activeKey: string, selector = '[data-pill-active="true"]') {
  const bar = useRef<HTMLDivElement>(null);
  const pill = useRef<HTMLSpanElement>(null);

  const move = useCallback((animate: boolean) => {
    const barNode = bar.current;
    const pillNode = pill.current;
    if (!barNode || !pillNode) return;
    const active = barNode.querySelector<HTMLElement>(selector);
    if (!active) return;

    const apply = () => {
      pillNode.style.transform = `translateX(${active.offsetLeft - barNode.clientLeft}px)`;
      pillNode.style.width = `${active.offsetWidth}px`;
    };

    if (animate) {
      apply();
    } else {
      const previous = pillNode.style.transition;
      pillNode.style.transition = "none";
      apply();
      void pillNode.offsetWidth;
      pillNode.style.transition = previous;
    }
    pillNode.dataset.ready = "true";
  }, [selector]);

  useLayoutEffect(() => {
    const frame = requestAnimationFrame(() => move(pill.current?.dataset.ready === "true"));
    return () => cancelAnimationFrame(frame);
  }, [activeKey, move]);

  /**
   * Re-measure whenever the bar itself changes size, not only on window resize.
   * Several of these controls live inside a collapsed accordion or a panel that
   * reflows, and a pill measured while its bar had no width would otherwise stay
   * stuck at zero until the next selection.
   */
  useEffect(() => {
    const barNode = bar.current;
    if (!barNode) return;
    const observer = new ResizeObserver(() => move(false));
    observer.observe(barNode);
    const onResize = () => move(false);
    window.addEventListener("resize", onResize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", onResize);
    };
  }, [move]);

  return {
    barRef: bar,
    pill: <span className="t-tabs-pill" ref={pill} data-ready="false" aria-hidden="true" />
  };
}

/* ------------------------------------------------------------------ *
 * 11 · Group hover
 * ------------------------------------------------------------------ */

/**
 * Distance-falloff lift across a horizontal row.
 *
 * Hovering one card lifts it and nudges its neighbours less the further away
 * they are. The timing function is written inline immediately *before* the
 * variables change, because the browser uses whichever curve is current at the
 * moment a transitionable property is touched — that is what gives a clean lift
 * on the way in and a springy settle on the way back without a second class.
 */
export function HoverGroup({ children, className }: { children: ReactNode[]; className?: string }) {
  const root = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  const setShifts = useCallback((activeIndex: number | null, phase: "in" | "out") => {
    const node = root.current;
    if (!node || reduced) return;
    const lift = readNumber("--avatar-lift", -4);
    const falloff = readNumber("--avatar-falloff", 0.45);
    const scale = readNumber("--avatar-scale", 1.05);
    const timing = phase === "out"
      ? readEase("--avatar-ease-out", "cubic-bezier(0.34, 3.85, 0.64, 1)")
      : readEase("--avatar-ease-in", "cubic-bezier(0.22, 1, 0.36, 1)");

    node.querySelectorAll<HTMLElement>(".t-avatar").forEach((item, index) => {
      item.style.transitionTimingFunction = timing;
      if (activeIndex === null) {
        item.style.setProperty("--shift", "0px");
        item.style.setProperty("--scale-active", "1");
        return;
      }
      const distance = Math.abs(index - activeIndex);
      item.style.setProperty("--shift", `${(lift * Math.pow(falloff, distance)).toFixed(3)}px`);
      item.style.setProperty("--scale-active", index === activeIndex ? String(scale) : "1");
    });
  }, [reduced]);

  return (
    <div className={className} ref={root} onMouseLeave={() => setShifts(null, "out")}>
      {children.map((child, index) => (
        <div className="t-avatar" key={index} onMouseEnter={() => setShifts(index, "in")}>
          {child}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 19 · Card hover tilt
 * ------------------------------------------------------------------ */

const MAX_TILT = 7;

/**
 * A card that leans toward the pointer.
 *
 * The pointer is tracked on the flat outer wrapper, which never transforms. If
 * it were tracked on the rotating card, the tilted edges would slip out from
 * under the cursor near the borders and the hover would flicker on and off.
 *
 * The lean is deliberately shallow. These are figures to be read, not artwork,
 * and a steep tilt makes tabular numbers hard to scan.
 */
export function TiltCard({ children, className }: { children: ReactNode; className?: string }) {
  const outer = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  const reset = useCallback(() => {
    outer.current?.classList.remove("is-hover");
    card.current?.classList.remove("is-tilting");
    card.current?.style.setProperty("--tilt-rx", "0deg");
    card.current?.style.setProperty("--tilt-ry", "0deg");
  }, []);

  const track = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (reduced) return;
    const wrapper = outer.current;
    const surface = card.current;
    if (!wrapper || !surface) return;
    const rect = wrapper.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    wrapper.classList.add("is-hover");
    surface.classList.add("is-tilting");
    surface.style.setProperty("--tilt-ry", `${((x - 0.5) * MAX_TILT).toFixed(2)}deg`);
    surface.style.setProperty("--tilt-rx", `${((0.5 - y) * MAX_TILT).toFixed(2)}deg`);
    surface.style.setProperty("--tilt-gx", `${(x * 100).toFixed(1)}%`);
    surface.style.setProperty("--tilt-gy", `${(y * 100).toFixed(1)}%`);
  }, [reduced]);

  return (
    <div
      className={className ? `t-tilt ${className}` : "t-tilt"}
      ref={outer}
      onPointerMove={track}
      onPointerUp={reset}
      onPointerCancel={reset}
      onPointerLeave={event => { if (event.pointerType === "mouse") reset(); }}
    >
      <div className="t-tilt-card" ref={card}>
        {children}
        <div className="t-tilt-glare" aria-hidden="true" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 03 · Notification badge
 * ------------------------------------------------------------------ */

/**
 * A count riding on top of a trigger.
 *
 * Only the badge slides and pops — the trigger it is anchored to never moves,
 * so a navigation row does not jump when a run starts needing attention.
 * The trigger must be a positioned ancestor.
 */
export function Badge({ count, tone = "accent", label }: {
  count: number;
  tone?: "accent" | "negative" | "warning";
  label: string;
}) {
  const open = count > 0;
  return (
    <span className={tone === "accent" ? "t-badge" : `t-badge tone-${tone}`} data-open={open}>
      {/* The dot stays mounted so it can animate away as well as in, but it is
          only announced while there is actually something to report — an empty
          live region would otherwise sit on every navigation row. */}
      <span className="t-badge-dot" role={open ? "status" : undefined} aria-label={open ? label : undefined}>
        {open ? (count > 99 ? "99+" : count) : ""}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 20 · Trigger-to-menu morph
 * ------------------------------------------------------------------ */

/**
 * A control that becomes the surface it opens.
 *
 * Chosen over a dropdown because the trigger and the panel are the same
 * element: the button's box grows into the menu instead of a separate popover
 * appearing beside it. `overflow: hidden` on the container is what clips the
 * menu while the box is still growing.
 */
export function MorphMenu({ label, icon, children, className, disabled = false }: {
  label: string;
  icon: ReactNode;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
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

  return (
    <div
      className={className ? `t-morph ${className}` : "t-morph"}
      data-open={open}
      ref={container}
    >
      <div className="t-morph-menu" id={menuId} role="menu" aria-label={label} aria-hidden={!open}>
        {children}
      </div>
      <button
        type="button"
        className="t-morph-plus"
        aria-expanded={open}
        aria-controls={menuId}
        disabled={disabled}
        onClick={() => setOpen(value => !value)}
      >
        {icon}{label}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 08 · Page transition
 * ------------------------------------------------------------------ */

/**
 * The entrance half of the side-by-side page slide.
 *
 * Only one workspace section is mounted at a time, so there is no outgoing page
 * to hold in a second absolute layer. The incoming surface enters from the side
 * it is travelling from — moving down the navigation slides one way, moving back
 * up slides the other — which keeps the sections feeling spatially arranged.
 */
export function PageEnter({ direction, children }: {
  direction: "forward" | "backward";
  children: ReactNode;
}) {
  return <div className="t-page-enter" data-direction={direction}>{children}</div>;
}

/**
 * Tracks which way a selection moved through an ordered list, so a transition
 * can enter from the correct side.
 */
export function useTravelDirection(value: string, order: readonly string[]) {
  const previous = useRef(value);
  const direction = useRef<"forward" | "backward">("forward");

  if (previous.current !== value) {
    const from = order.indexOf(previous.current);
    const to = order.indexOf(value);
    direction.current = to >= from ? "forward" : "backward";
    previous.current = value;
  }

  return direction.current;
}

/* ------------------------------------------------------------------ *
 * 13 · Input clear with dissolve
 * ------------------------------------------------------------------ */

/** Samples a CSS cubic-bezier so the JS envelope matches the stylesheet. */
function bezier(spec: string) {
  const match = spec.match(/cubic-bezier\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)/);
  if (!match) return (t: number) => t;
  const [x1, y1, x2, y2] = match.slice(1).map(Number);
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  return (t: number) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    let s = t;
    for (let i = 0; i < 8; i += 1) {
      const dx = ((ax * s + bx) * s + cx) * s - t;
      const d = (3 * ax * s + 2 * bx) * s + cx;
      if (Math.abs(dx) < 1e-6 || d === 0) break;
      s -= dx / d;
    }
    return ((ay * s + by) * s + cy) * s;
  };
}

/**
 * Clearing a text field, with the value dissolving out.
 *
 * The typed text flies down and blurs while a soft streak ignites beneath each
 * word and the placeholder falls in from above. The streak's rise, peak and fall
 * cannot be expressed as static keyframes, so this one recipe runs per frame.
 *
 * On a dark surface the streak lightens with `screen` blending and paints white
 * gradients; `multiply` — the light-mode form — would simply vanish here.
 */
export function useClearDissolve(
  value: string,
  onCleared: () => void,
  inputRef: RefObject<HTMLInputElement | null>
) {
  const wrap = useRef<HTMLSpanElement>(null);
  const clearing = useRef(false);
  const reduced = useReducedMotion();

  /**
   * The mirror owns the visible glyphs, so it has to track the value as it is
   * typed — but not during a clear, when it is deliberately holding the outgoing
   * text while that text flies away. React never renders children into it for
   * the same reason: the two would fight over the same node mid-animation.
   */
  useEffect(() => {
    if (clearing.current) return;
    const mirror = wrap.current?.querySelector<HTMLElement>(".t-clear-mirror");
    if (mirror) mirror.textContent = value.replace(/ /g, "\u00a0");
  }, [value]);

  const clear = useCallback(() => {
    const wrapper = wrap.current;
    const input = inputRef.current;
    if (!wrapper || !input || clearing.current || !value) return;

    if (reduced) {
      onCleared();
      return;
    }

    const mirror = wrapper.querySelector<HTMLElement>(".t-clear-mirror");
    const placeholder = wrapper.querySelector<HTMLElement>(".t-clear-placeholder");
    const glow = wrapper.querySelector<HTMLElement>(".t-clear-glow");
    if (!mirror || !placeholder || !glow) {
      onCleared();
      return;
    }

    clearing.current = true;
    const keepFocus = document.activeElement === input;
    const text = value.replace(/ /g, "\u00a0");
    mirror.textContent = text;

    const total = readMs("--clear-dur", 1000);
    const outDuration = readMs("--clear-out-dur", 400);
    const inDuration = readMs("--clear-in-dur", 400);
    const outFly = readNumber("--clear-out-fly", 12);
    const inFly = readNumber("--clear-in-fly", 12);
    const blur = readNumber("--clear-blur", 2);
    const delay = readMs("--glow-delay", 50);
    const peakAt = readNumber("--glow-peak-at", 0.15);
    const peakOpacity = readNumber("--glow-opacity", 0.85);
    const spread = readNumber("--glow-spread", 1.5);
    const easeOut = bezier(readEase("--clear-out-ease", "cubic-bezier(0.22, 1, 0.36, 1)"));
    const easeIn = bezier(readEase("--clear-in-ease", "cubic-bezier(0.22, 1, 0.36, 1)"));

    // One radial layer per word, positioned by measuring the real glyph run.
    const measure = document.createElement("canvas").getContext("2d");
    if (measure) measure.font = getComputedStyle(input).font;
    const width = wrapper.clientWidth || 280;
    const padLeft = parseFloat(getComputedStyle(input).paddingLeft) || 12;
    const layers: string[] = [];
    let cursor = 0;
    text.split(/(\s+)/).forEach(segment => {
      const segmentWidth = measure?.measureText(segment).width ?? segment.length * 7;
      if (segment.trim()) {
        const centre = padLeft + cursor + segmentWidth / 2;
        const halfWidth = Math.max(segmentWidth * 0.45, 8) * spread;
        ([[0, 0.8, 7, 0.22], [halfWidth * 0.45, 0.55, 8, 0.18],
          [-halfWidth * 0.4, 0.65, 6, 0.16], [halfWidth * 0.15, 0.9, 5, 0.14]] as const)
          .forEach(([dx, widthMultiplier, height, alpha]) => {
            const left = (((centre + dx) / width) * 100).toFixed(2);
            layers.push(
              `radial-gradient(ellipse ${Math.max(halfWidth * widthMultiplier, 2).toFixed(1)}px ${height}px at ${left}% 100%, rgb(255 255 255 / ${alpha}), transparent)`
            );
          });
      }
      cursor += segmentWidth;
    });

    onCleared();
    wrapper.classList.add("is-clearing");
    glow.style.background = layers.join(", ");
    glow.style.opacity = "0";

    const started = performance.now();
    const tick = (now: number) => {
      const elapsed = now - started;
      const out = easeOut(Math.min(1, elapsed / outDuration));
      mirror.style.transform = `translateY(${(out * outFly).toFixed(1)}px)`;
      mirror.style.opacity = (1 - out).toFixed(3);
      mirror.style.filter = `blur(${(out * blur).toFixed(1)}px)`;

      const entering = easeIn(Math.min(1, elapsed / inDuration));
      placeholder.style.transform = `translateY(${(-inFly + entering * inFly).toFixed(1)}px)`;
      placeholder.style.opacity = (0.9 + entering * 0.1).toFixed(3);
      placeholder.style.filter = `blur(${(blur - entering * blur).toFixed(1)}px)`;

      let envelope = 0;
      if (elapsed > delay) {
        const progress = Math.min(1, (elapsed - delay) / Math.max(1, total - delay));
        envelope = progress < peakAt ? progress / peakAt : 1 - (progress - peakAt) / (1 - peakAt);
      }
      glow.style.opacity = (envelope * peakOpacity).toFixed(3);

      if (elapsed < total) {
        requestAnimationFrame(tick);
        return;
      }
      wrapper.classList.remove("is-clearing");
      [mirror, placeholder].forEach(element => { element.style.cssText = ""; });
      mirror.textContent = "";
      glow.style.opacity = "0";
      glow.style.background = "";
      clearing.current = false;
      if (keepFocus) requestAnimationFrame(() => input.focus({ preventScroll: true }));
    };
    requestAnimationFrame(tick);
  }, [inputRef, onCleared, reduced, value]);

  return { wrapRef: wrap, clear };
}

/**
 * A text field with a clear control.
 *
 * Emptying a field you have typed into is destructive in miniature, and the
 * dissolve makes it legible: the value flies down and blurs away rather than
 * vanishing between frames, so it is obvious that something was removed and not
 * that the field failed to accept it.
 *
 * Pressing the control does not steal focus from the input, so clearing and
 * retyping stays one gesture.
 */
export function ClearableInput({ value, onChange, onClear, clearLabel, placeholder, ...rest }: {
  value: string;
  onChange: (value: string) => void;
  /** Defaults to clearing through `onChange`. */
  onClear?: () => void;
  clearLabel: string;
  placeholder?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "placeholder">) {
  const input = useRef<HTMLInputElement>(null);
  const { wrapRef, clear } = useClearDissolve(value, () => (onClear ? onClear() : onChange("")), input);

  const keepFocus = (event: { preventDefault: () => void }) => {
    if (document.activeElement === input.current) event.preventDefault();
  };

  return (
    <span className={value ? "t-clear has-value" : "t-clear"} ref={wrapRef}>
      <input
        ref={input}
        value={value}
        placeholder={placeholder}
        onChange={event => onChange(event.target.value)}
        {...rest}
      />
      {/* Filled imperatively by the hook — see the note above. */}
      <span className="t-clear-mirror" aria-hidden="true" />
      <span className="t-clear-placeholder" aria-hidden="true">{placeholder}</span>
      <span className="t-clear-glow" aria-hidden="true" />
      {value !== "" && (
        <button
          type="button"
          className="t-clear-btn"
          aria-label={clearLabel}
          onPointerDown={keepFocus}
          onMouseDown={keepFocus}
          onClick={clear}
        >
          <X aria-hidden="true" />
        </button>
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * 23 · Fill and burst
 * ------------------------------------------------------------------ */

const PARTICLE_COUNT = 8;

/**
 * The celebration half of the like recipe, driven by a real boolean.
 *
 * Fires when a value that was unsaved becomes saved. Losing the state just
 * drops the fill: the burst belongs to the way in only, so an edit does not
 * throw particles every time it invalidates the stored copy.
 */
export function useBurst(active: boolean) {
  const host = useRef<HTMLButtonElement>(null);
  const previous = useRef(active);
  const reduced = useReducedMotion();

  useEffect(() => {
    const node = host.current;
    const became = active && !previous.current;
    previous.current = active;
    if (!node || !became || reduced) return;

    const distance = readNumber("--like-particle-dist", 20);
    const duration = readMs("--like-particle-dur", 600);
    node.querySelectorAll<HTMLElement>(".t-like-particles i").forEach((particle, index) => {
      const angle = (index / PARTICLE_COUNT) * Math.PI * 2 + Math.random() * 0.4;
      const reach = distance * (0.7 + Math.random() * 0.6);
      particle.style.setProperty("--px", `${(Math.cos(angle) * reach).toFixed(1)}px`);
      particle.style.setProperty("--py", `${(Math.sin(angle) * reach).toFixed(1)}px`);
      particle.style.setProperty("--pdur", `${Math.round(duration * (0.75 + Math.random() * 0.5))}ms`);
      particle.style.setProperty("--pdelay", `${Math.round(Math.random() * 60)}ms`);
      particle.style.setProperty("--psize", (0.7 + Math.random() * 0.8).toFixed(2));
      particle.style.setProperty("--p-end-scale", (0.3 + Math.random() * 0.5).toFixed(2));
    });

    node.classList.remove("is-bursting");
    void node.offsetWidth;
    node.classList.add("is-bursting");
    const timer = window.setTimeout(() => node.classList.remove("is-bursting"), duration + 200);
    return () => window.clearTimeout(timer);
  }, [active, reduced]);

  return {
    hostRef: host,
    particles: (
      <span className="t-like-particles" aria-hidden="true">
        {Array.from({ length: PARTICLE_COUNT }, (_, index) => <i key={index} />)}
      </span>
    )
  };
}
