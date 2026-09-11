# BTC chart navigation, 2026-09-11

The public market page and the dashboard share `BtcMarketChart`. Both use the existing read-only Binance REST snapshot and WebSocket stream, capped at 240 candles. Trading, authentication, analysis metrics, order books, and Delta market context are unchanged by this update.

The old null zoom state meant the latest 80 candles, but Fit all and maximum zoom-out also used it. The wheel listener ran before the SVG existed on a cold load. Index-based history drifted when a new candle displaced the oldest candle in the full buffer. Touch and pointer handlers independently changed the same range, and clicking the minimap outside its window left the drag origin on the previous selection.

`lib/chart-viewport.ts` now models latest, history, and all views explicitly. History is anchored by candle open time. Latest follows the feed at the selected candle count. If the selected history expires from the rolling buffer, the viewport clamps to the oldest available candles. This update does not fetch older history.

`useChartViewport` owns pointer gestures, wheel normalization, keyboard commands, SVG measurement, and animation-frame batching. Its callback ref attaches and removes the non-passive wheel listener with the actual SVG. Pointer capture, cancellation, and a shared pan/pinch gesture keep mouse and touch input on one path.

Controls have a separate wrapping row, visible labels, and larger targets. Fit all shows all loaded candles; Latest preserves zoom; Reset returns to the latest 80. The minimap has keyboard-operable edge sliders. The chart uses its measured width to keep labels readable and reduce time-axis crowding. Price scaling remains automatic for the visible candles.

Verification uses `node --test scripts/chart-viewport.test.mjs`, TypeScript, lint, and the production build. The seven regression tests cover range limits, pointer anchors, complete zoom-out, rolling history, live following, minimap resizing, and short or empty datasets. No browser was used, as requested. Visual rendering and physical mouse/touch behavior still need a manual device check.
