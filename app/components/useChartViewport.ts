"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import {
  DEFAULT_VISIBLE, MIN_VISIBLE, moveRange, plotRatio, resolveViewport, viewportFromRange, zoomRange,
  type CandleRange, type ChartViewport,
} from "@/lib/chart-viewport";

type Point = { x: number; y: number };
type Gesture =
  | { kind: "pan"; origin: Point; viewport: ChartViewport }
  | { kind: "pinch"; distance: number; ratio: number; viewport: ChartViewport };

const INITIAL_VIEW: ChartViewport = { kind: "latest", count: DEFAULT_VISIBLE };
const LEFT = 18;
const RIGHT = 88;

export function useChartViewport(candles: readonly { openTime: number }[], interval: string) {
  const [selection, setSelection] = useState({ interval, viewport: INITIAL_VIEW });
  const viewport = selection.interval === interval ? selection.viewport : INITIAL_VIEW;
  const range = resolveViewport(viewport, candles);
  const [width, setWidth] = useState(1200);
  const [isDragging, setIsDragging] = useState(false);
  const current = useRef({ candles, interval, viewport, width });
  const points = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture | null>(null);
  const frame = useRef<number | null>(null);
  const wheelDelta = useRef(0);
  const panDelta = useRef(0);

  useLayoutEffect(() => {
    const pending = frame.current !== null && current.current.interval === interval;
    current.current = { candles, interval, viewport: pending ? current.current.viewport : viewport, width };
  }, [candles, interval, viewport, width]);

  const commit = useCallback((next: ChartViewport) => {
    current.current.viewport = next;
    // High-frequency gestures share one render per animation frame. The ref
    // advances immediately, so rapid wheel events build on the pending range.
    if (frame.current === null) frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const state = current.current;
      setSelection({ interval: state.interval, viewport: state.viewport });
    });
  }, []);

  const changeRange = useCallback((next: CandleRange) => {
    commit(viewportFromRange(next, current.current.candles));
  }, [commit]);

  const cancelGesture = useCallback(() => {
    points.current.clear();
    gesture.current = null;
    setIsDragging(false);
  }, []);

  useEffect(() => {
    cancelGesture();
    wheelDelta.current = 0;
    panDelta.current = 0;
    setSelection({ interval, viewport: INITIAL_VIEW });
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [interval, cancelGesture]);

  const svgRef = useCallback((svg: SVGSVGElement | null) => {
    if (!svg) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.max(320, Math.round(entry.contentRect.width)));
    });
    observer.observe(svg);
    const wheel = (event: WheelEvent) => {
      const state = current.current;
      if (!state.candles.length || points.current.size || event.metaKey || event.altKey) return;
      const rect = svg.getBoundingClientRect();
      const ratio = plotRatio(event.clientX, rect.left, rect.width, state.width, LEFT, RIGHT);
      const active = resolveViewport(state.viewport, state.candles);
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
      if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        const delta = (event.deltaX || event.deltaY) * unit;
        if (!delta) return;
        event.preventDefault();
        wheelDelta.current = 0;
        if (Math.sign(panDelta.current) !== Math.sign(delta)) panDelta.current = 0;
        panDelta.current += delta / Math.max(1, rect.width * (state.width - LEFT - RIGHT) / state.width) * (active.end - active.start);
        const shift = Math.trunc(panDelta.current);
        if (!shift) return;
        panDelta.current -= shift;
        changeRange(moveRange(active, shift, state.candles.length));
        return;
      }
      if (!event.deltaY) return;
      event.preventDefault();
      panDelta.current = 0;
      if (Math.sign(wheelDelta.current) !== Math.sign(event.deltaY)) wheelDelta.current = 0;
      wheelDelta.current += event.deltaY * unit;
      if (Math.abs(wheelDelta.current) < 8) return;
      const delta = Math.max(-120, Math.min(120, wheelDelta.current));
      const next = zoomRange(active, Math.exp(delta * .003), ratio, state.candles.length);
      const count = active.end - active.start;
      const atLimit = delta > 0 ? count >= state.candles.length : count <= MIN_VISIBLE;
      if (next.end - next.start === count && !atLimit) return;
      wheelDelta.current = 0;
      changeRange(next);
    };
    svg.addEventListener("wheel", wheel, { passive: false });
    // Callback-ref cleanup runs on loading/error transitions as well as unmount.
    return () => {
      observer.disconnect();
      svg.removeEventListener("wheel", wheel);
    };
  }, [changeRange]);

  const beginGesture = (svg: SVGSVGElement) => {
    const [first, second] = [...points.current.values()];
    const state = current.current;
    if (first && second) {
      const rect = svg.getBoundingClientRect();
      gesture.current = {
        kind: "pinch", distance: Math.max(1, Math.hypot(second.x - first.x, second.y - first.y)),
        ratio: plotRatio((first.x + second.x) / 2, rect.left, rect.width, state.width, LEFT, RIGHT),
        viewport: state.viewport,
      };
    } else gesture.current = first ? { kind: "pan", origin: first, viewport: state.viewport } : null;
  };

  const onPointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 || points.current.size >= 2) return;
    points.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    event.currentTarget.setPointerCapture(event.pointerId);
    beginGesture(event.currentTarget);
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (!points.current.has(event.pointerId)) return false;
    points.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const start = gesture.current;
    if (!start) return false;
    const state = current.current;
    const active = resolveViewport(start.viewport, state.candles);
    const rect = event.currentTarget.getBoundingClientRect();
    const plotWidth = rect.width * (state.width - LEFT - RIGHT) / state.width;
    if (plotWidth <= 0) return false;
    if (start.kind === "pinch") {
      const [first, second] = [...points.current.values()];
      if (!first || !second) return false;
      const distance = Math.max(1, Math.hypot(second.x - first.x, second.y - first.y));
      const next = zoomRange(active, start.distance / distance, start.ratio, state.candles.length);
      const ratio = plotRatio((first.x + second.x) / 2, rect.left, rect.width, state.width, LEFT, RIGHT);
      changeRange(moveRange(next, (start.ratio - ratio) * (next.end - next.start), state.candles.length));
    } else {
      const dx = event.clientX - start.origin.x;
      if (Math.abs(dx) < 3) return false;
      changeRange(moveRange(active, -dx / plotWidth * (active.end - active.start), state.candles.length));
    }
    setIsDragging(true);
    return true;
  };

  const onPointerUp = (event: PointerEvent<SVGSVGElement>) => {
    if (!points.current.delete(event.pointerId)) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    beginGesture(event.currentTarget);
    setIsDragging(false);
  };

  const zoom = (factor: number) => {
    const state = current.current;
    const active = resolveViewport(state.viewport, state.candles);
    changeRange(zoomRange(active, factor, state.viewport.kind === "latest" ? 1 : .5, state.candles.length));
  };
  const pan = (shift: number) => {
    const state = current.current;
    changeRange(moveRange(resolveViewport(state.viewport, state.candles), shift, state.candles.length));
  };
  const latest = () => {
    const active = resolveViewport(current.current.viewport, current.current.candles);
    commit({ kind: "latest", count: active.end - active.start });
  };
  const reset = () => commit(INITIAL_VIEW);
  const fitAll = () => commit({ kind: "all" });
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || event.ctrlKey || event.metaKey || event.altKey || !candles.length) return;
    const step = event.shiftKey ? 10 : 1;
    switch (event.key) {
      case "ArrowLeft": pan(-step); break;
      case "ArrowRight": pan(step); break;
      case "+": case "=": zoom(1 / 1.3); break;
      case "-": zoom(1.3); break;
      case "Home": fitAll(); break;
      case "End": latest(); break;
      case "0": reset(); break;
      default: return;
    }
    event.preventDefault();
  };

  return { range, width, isDragging, svgRef, changeRange, zoom, pan, latest, reset, fitAll, onKeyDown,
    onPointerDown, onPointerMove, onPointerUp };
}
