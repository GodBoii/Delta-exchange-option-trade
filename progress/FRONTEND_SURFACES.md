# Frontend surfaces, 2026-09-11

Removed the repeated enclosing frames across shared panels, portfolio metrics, Builder
sections, market data, News, Automation charts, sign-in, connection, and password recovery.
Mobile table records now use row separators. Existing form controls, overlays, selection
indicators, and risk/error feedback retain their boundaries.

Page subtitles and redundant Builder hints were removed. The chart's gesture instructions
remain available to assistive technology without occupying screen space. Its toolbar fits
on one row at 390px, with a labelled reset icon. News history no longer uses hover-lift
wrappers and portfolio figures no longer tilt. Mobile navigation and the account trigger
now have explicit accessible names even when their visible text is hidden.

The production site was inspected read-only for Builder, Portfolio, History, News, and
Automation. Local Builder and Market were checked at desktop and 390px widths. The chart
rendered live market data; Fit all selected 240 candles and Reset restored 80. The mobile
toolbar measured 44px high, the outer chart border measured 0px, and the page had no
horizontal overflow. Light theme and account-menu Escape dismissal were also checked.

The production build, TypeScript, focused ESLint, and the seven chart range regression tests passed. The local
trading backend was unavailable, so production-only portfolio and execution flows were
audited through their components and existing production UI rather than exercised with
the updated stylesheet. No trading or account data was modified.
