# Frontend visual audit

Date: 20 October 2026

The screenshots show a sizing problem shared across the interface, plus a separate
subaccount component inconsistency. This audit covers the frontend shell, builder,
capital policy, subaccounts, history, market, automation, P&L, owner controls,
authentication, shared controls, typography, responsive rules and motion. Findings
below distinguish code inspection from browser verification. This is not a claim
that every authenticated production state has been exercised.

## Architecture and scope

Next.js 15 and React 19 use TypeScript and one global CSS stylesheet. There is no
Tailwind migration or new UI library. `ui.tsx` provides fields, listboxes, switches,
segmented controls, panels, dialogs and feedback. `AppShell.tsx` owns desktop top
navigation, the mobile dock, profile navigation and command search. The root layout
self hosts Instrument Sans, Instrument Serif and JetBrains Mono. These are already
intentional fonts; replacing them just to make a font swap would add churn.

`app/page.tsx` gates destinations by authentication, connection and owner access.
Feature components call typed frontend helpers and authenticated backend endpoints.
Capital settings are also reused by the owner editor. Subaccounts validates API
responses through `lib/subaccounts.ts` and accepts an injected requester. Backend
trade execution, stored strategy contracts, wallet calculation and credential
handling do not need to change to correct these layouts.

The visual direction is a compact trading instrument: amber action accents,
distinct asset blocks, ledger alignment, small serif page titles and monospace
financial figures. Effects describe an action or state change. Prices retain their
real meaning; decoration does not substitute for data.

## Findings and changes

| # | Finding and evidence | Resolution |
| --- | --- | --- |
| 1 | `Select` filled its grid cell without a width limit. Capital default and overall cap stretched across the entire form. | Shared select fields stop at 24rem and shrink with their container. |
| 2 | Popup width copied the trigger, making a short list span much of a monitor. | Popups remain anchored to the now bounded trigger. |
| 3 | Subaccounts used five native selects while other pages used the themed listbox. The OS painted the blue selected option shown in the screenshots. | Management, participation, run assignment, transfer source and destination use the shared Select. No native selects remain in that component. |
| 4 | `.button-row > .button` used `flex: 1`, expanding routine actions to consume every spare pixel. | Actions size to their label and wrap. Explicit block actions keep their full width. |
| 5 | Grid items stretched buttons, including Verify and save key. | Buttons align to the start of a grid cell. Credential forms also have a 36rem reading width. |
| 6 | Switches occupied an entire form row for an On/Off state. | Switch hit areas remain accessible but size to their content. |
| 7 | Tiny numeric values, such as a five minute buffer or concurrent strategy count, used enormous inputs. | Shared numeric controls stop at 12rem. Their explanatory text remains readable. |
| 8 | Risk basis and the emergency exit switch each consumed a full width row. | They share the existing responsive two column grid and stack on phones. |
| 9 | Risk grid minimum columns exceeded the builder width on some small laptops. `overflow: hidden` could hide the overflow. | Columns now wrap according to available space, retaining existing phone overrides. |
| 10 | The workspace expanded to 1760px on wide monitors, enlarging forms without increasing useful information. | A shared 1480px maximum aligns navigation with the workspace and keeps a usable form and review rail. Charts still use the available workspace. |
| 11 | The sticky builder rail started below only the first navigation row. Its top could slide under the second row. | Sticky offset includes both header rows. |
| 12 | Large serif page headings and decorative ruler spacing pushed working controls downward. | Retain the serif identity at 32px desktop and 28px phone, with a compact divider and tighter spacing. Marketing entry typography stays separate. |
| 13 | BTC and ETH capital overrides were similar anonymous form columns. | Asset sections have coin marks, ticker headings, a fine amber edge and distinct section boundaries. |
| 14 | Capital figures floated without a clear boundary between editable policy and current wallet state. | Live wallet figures have a quiet summary panel. |
| 15 | Asset budget totals and explanatory amounts were centered or awkwardly inset, partly due to definition list indentation. | Reset definition margins; align totals and split next entry, reserved and cap into readable label/value rows. |
| 16 | Helper text used `#7a7977`, which became faint on raised dark surfaces. | Lift the dark secondary metadata token to `#969590`. Light theme retains its own token. |
| 17 | The stylesheet claimed a 12px text floor but contained 32 declarations at 11px, plus a 10px narrow dock label. | Those text sizes now use the existing 12px token. |
| 18 | Primary actions shared a generic white gradient treatment. | Use the product amber and an asymmetric ticket corner, with theme aware contrast and hover. |
| 19 | Dropdown selection depended heavily on a subtle text change and tick. | Add a fine selected edge and amber tint while retaining the tick and keyboard highlight. |
| 20 | Menu placement guessed height from option count, ignoring wrapped hints and group headings. Short menus opening upward could leave a large gap. | Measure rendered content before paint and anchor the actual popup height. |
| 21 | Menu max height ignored the space available on a short landscape viewport; horizontal placement was not clamped. | `lib/listbox-layout.ts` caps the height on the roomier side and clamps both horizontal edges. |
| 22 | An open listbox intercepted Tab, preventing normal forward focus movement. | Tab closes the popup and lets the browser advance focus. Escape restores trigger focus. |
| 23 | `aria-activedescendant` was on the popup while keyboard focus stayed on the trigger. | The focused combobox now identifies the active enabled option. Helper text is associated with it. |
| 24 | A disabled selected option could become the initial keyboard target; commit lacked an enabled option check. | Open on an enabled option and validate keyboard commits against enabled options. |
| 25 | An old close timeout could remove a newly reopened menu. | Cancel pending close on reopen and clean up on unmount. Reset typeahead on open. |
| 26 | A closing menu remained exposed to assistive technology during its exit animation. | Hide the closing listbox from the accessibility tree. |
| 27 | A run picker with no available runs offered an empty dropdown. | Disable it and explain how to load unassigned runs. |
| 28 | API fields used detached labels and separate spacing instead of shared Field components. | Use the shared field layout and preserve validation, password input and autocomplete behavior. |
| 29 | Run assignment and key replacement used native disclosure triangles that differed from nearby animated sections. | Reuse the existing disclosure, chevron, inert state and reduced motion handling. |
| 30 | The accordion's open CSS selectors matched every descendant. Browser testing reproduced a parent revealing closed nested content and flipping closed child chevrons. | Apply panel motion to each panel's own `aria-hidden` state, and chevron motion to its own expanded button. |
| 31 | Changing a transfer source to the existing destination left an invalid stale destination selected. | Clear the destination when the source changes to it. Existing submit guards remain. |
| 32 | Mobile dropdown rows had no explicit minimum touch height. | Options have a 44px minimum on phones; triggers keep 16px text to avoid form zoom. |

## Remaining issues

These are recorded rather than presented as completed work.

| Area | Issue | Next useful change |
| --- | --- | --- |
| Subaccount destructive actions | Position closing, order cancellation, key removal and transfers still use native `window.confirm`. | Move each to the existing ConfirmModal with account and amount context, then separately verify cancellation, duplicate submission and failure handling. Financial confirmation behavior deserves a focused change. |
| Large libraries | The strategy picker scrolls groups and supports typeahead but has no explicit search input. | Add filtering when real libraries become large enough to justify another control. Keep all existing keyboard semantics. |
| Narrow builder workflow | The review rail follows the complete form when stacked. Users must scroll to review and schedule. | Consider a small review jump action or mobile summary drawer, with validation focus and scroll restoration checks. |
| Motion density | The shared layer contains many recipes, including blur reveals, digit reels and save particles. Those can compete during frequent updates. | Measure real automation and portfolio sessions; reduce repeated emphasis where values update rapidly. Preserve reduced motion and avoid adding continuous effects to routine controls. |
| CSS maintenance | One stylesheet has nearly 9000 lines, including identity overrides that redefine earlier headings and multiple breakpoint layers. | Move component rules into scoped styles gradually. Avoid an unrelated stylesheet rewrite during a UI repair. |
| Overlay layering | Overlay levels use repeated literals such as 60, 100, 110, 120 and 130. | Consolidate into named layer tokens when auditing dialogs, menus and the command palette together. |
| Empty state consistency | Some account tables have illustrated empty states while positions and orders use plain copy. | Compose a quieter shared empty row where a next action actually exists; avoid decorative illustrations in every empty list. |
| Discoverability | Mobile hides some detailed market metrics and leg reorder controls. | Decide which details need an explicit overflow action instead of hiding them, using real mobile workflows. |

The existing frontend already has a skip link, active navigation, native buttons,
semantic sections, loading placeholders, inline errors, focus treatments, a branded
favicon, theme tokens and reduced motion guards. Those are retained. The audit does
not invent missing features or add decorative noise, cookie banners, stock imagery
or legal links to authenticated trading forms without a product need.

## Validation

Production component checks use an isolated temporary local route with an injected
requester for capital and subaccounts. Fixture mutations never reach the trading
backend. The builder is rendered in its existing offline mode. The route is removed
before the production build and commits.

Geometry tests cover measured short menus, upward opening, short landscape screens,
right edge clipping and long lists. Existing capital, subaccount, strategy, portfolio
and reporting tests remain in the frontend suite.

Browser checks covered 320 × 740, 390 × 844, 720 × 320 landscape,
1024 × 900, 1440 × 1000 and 1920 × 1080 viewports. The changed capital,
subaccount and builder layouts had no document horizontal overflow in the checked
states. The wide workspace measured 1480px and its select and popup measured 384px.
The key save button measured about 149px wide with a 44px touch height on a phone.

Checks exercised capital edit/save, participation save, account management save,
empty run pickers, loading drafts, nested disclosures, negative transfer amounts,
valid transfer amounts and clearing a destination when it becomes the source.
No transfer or live trading action was submitted. Keyboard checks covered disabled
option skipping, selection, Escape, Tab advancing focus, End scrolling a grouped
list, outside dismissal and rapid close/reopen. Light and dark screenshots were
reviewed. A fresh load produced no browser error or warning logs. Existing reduced
motion CSS guards were inspected; the OS motion preference was not changed.

The production build, TypeScript, scoped ESLint and all 70 tests pass. Five of those
tests specifically cover listbox placement. The temporary fixture route is absent
from the final build. Screenshots use sample account and wallet data and are stored
outside the repository.
