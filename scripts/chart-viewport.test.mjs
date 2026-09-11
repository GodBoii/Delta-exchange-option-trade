import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Use the project's installed compiler; no browser or extra test dependency.
const source = await readFile(new URL("../lib/chart-viewport.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
});
const { clampRange, resolveViewport, viewportFromRange, zoomRange, moveRange, resizeRange, plotRatio } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const candles = (first = 0, count = 240) => Array.from({ length: count }, (_, i) => ({ openTime: (first + i) * 60_000 }));

test("default, fit all, and zoom out have distinct ranges", () => {
  const initial = resolveViewport({ kind: "latest", count: 80 }, candles());
  assert.deepEqual(initial, { start: 160, end: 240 });
  assert.deepEqual(resolveViewport({ kind: "all" }, candles()), { start: 0, end: 240 });
  let range = initial;
  for (let i = 0; i < 10; i++) range = zoomRange(range, 1.3, 1, 240);
  assert.deepEqual(range, { start: 0, end: 240 });
  assert.deepEqual(resolveViewport(viewportFromRange(range, candles()), candles()), range);
});

test("zoom holds the pointed candle and respects both limits", () => {
  assert.deepEqual(zoomRange({ start: 40, end: 120 }, .5, .25, 240), { start: 50, end: 90 });
  assert.deepEqual(zoomRange({ start: 160, end: 240 }, .5, 1, 240), { start: 200, end: 240 });
  assert.deepEqual(zoomRange({ start: 200, end: 240 }, .001, 1, 240), { start: 230, end: 240 });
});

test("history stays on the same timestamps when a full rolling buffer shifts", () => {
  const history = viewportFromRange({ start: 60, end: 100 }, candles());
  const next = resolveViewport(history, candles(1));
  assert.deepEqual(next, { start: 59, end: 99 });
  assert.equal(candles(1)[next.start].openTime, 60 * 60_000);
  assert.deepEqual(resolveViewport(history, candles(80)), { start: 0, end: 40 });
});

test("latest follows appends and rolling replacement without changing zoom", () => {
  const latest = viewportFromRange({ start: 210, end: 240 }, candles());
  assert.deepEqual(resolveViewport(latest, candles(0, 241)), { start: 211, end: 241 });
  assert.deepEqual(resolveViewport(latest, candles(1)), { start: 210, end: 240 });
});

test("pan clamps at history boundaries and minimap resize preserves the other edge", () => {
  const range = { start: 60, end: 100 };
  assert.deepEqual(moveRange(range, -1000, 240), { start: 0, end: 40 });
  assert.deepEqual(moveRange(range, 1000, 240), { start: 200, end: 240 });
  assert.deepEqual(resizeRange(range, "left", 1000, 240), { start: 90, end: 100 });
  assert.deepEqual(resizeRange(range, "right", -1000, 240), { start: 60, end: 70 });
  const recentered = clampRange(160, 40, 240);
  assert.deepEqual(moveRange(recentered, 5, 240), { start: 165, end: 205 });
});

test("empty and short histories never create indexes beyond the data", () => {
  for (const total of [0, 1, 5, 9, 10, 80, 240]) {
    for (const start of [-100, 0, 5, 1000]) {
      for (const count of [1, 10, 80, 500]) {
        const range = clampRange(start, count, total);
        assert.ok(range.start >= 0 && range.end <= total);
        assert.ok(range.end - range.start >= Math.min(10, total));
        for (const factor of [.001, .5, 1, 1.3, 100]) {
          const zoomed = zoomRange(range, factor, .5, total);
          assert.ok(zoomed.start >= 0 && zoomed.end <= total);
        }
      }
    }
  }
});

test("pointer anchors exclude the price axis and scale with the rendered width", () => {
  assert.equal(plotRatio(118, 100, 1200, 1200, 18, 88), 0);
  assert.equal(plotRatio(1212, 100, 1200, 1200, 18, 88), 1);
  assert.equal(plotRatio(382.5, 100, 600, 1200, 18, 88), .5);
  assert.equal(plotRatio(100, 0, 0, 1200, 18, 88), 0);
});
