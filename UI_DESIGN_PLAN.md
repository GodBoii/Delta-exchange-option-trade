# Interface design system

Reference for `app/globals.css` and the components in `app/components`. Read this before
adding a surface, so the next screen inherits the system instead of restating it.

## Structure

`app/globals.css` is a single pass in a fixed order: tokens → base → utilities → primitives →
shell → surfaces → responsive → motion. A selector is defined once. There are no "readability"
or "theme" override passes appended to the end of the file; if a value needs to change, it
changes at the token or at its single definition.

Components never hard-code a colour, radius, spacing step, or type size. Every value resolves
through a custom property.

## Colour

Three semantic families, and nothing else carries meaning:

| Family | Token | Meaning |
| --- | --- | --- |
| Interactive | `--accent` | Focus, current navigation, selected state, links |
| Long | `--long` | Buy side, positive P&L, healthy utilisation |
| Short | `--short` | Sell side, negative P&L, destructive actions |
| Caution | `--warn` | Degraded state, unsaved work, elevated risk |

Surfaces step through `--canvas` → `--surface-1..4` for nesting depth. Text steps through
`--text-1..3` for reading, support, and metadata. Primary buttons are near-white on dark
because the accent is reserved for state, not emphasis.

## Type

`--t-xs` (12px) is the floor for anything a user reads; the previous 8–10px labels are gone.
Numeric output uses tabular figures everywhere so streaming values do not shift columns.
Fonts are self-hosted through `next/font` — the app's own CSP blocks a Google Fonts
stylesheet, so an `@import` silently degrades to a system face.

## Layout

The authenticated app is a shell: a grouped navigation rail (`Operations` / `Research`), a
sticky context bar carrying the page title, the scheduling clock, and the account menu, and a
`--sidebar-w`-offset workspace. Below 960px the rail becomes a dismissible drawer.

The builder uses a two-column arrangement: configuration on the left, a sticky review rail on
the right holding the library, the derived summary, blocking issues, and the schedule action.
Commit actions live next to the review of what is being committed.

## Data display

Tables are real tables with `scope`, captions, right-aligned numeric columns, and semantic
side tags. Values are derived and labelled honestly:

- Wallet balances are listed per asset and never summed across denominations.
- Margin utilisation is a proportion of the balance it consumes.
- Liquidation distance is stated against entry price, because the REST position payload
  carries no mark price and inventing one would misstate live risk.
- Missing values render as an em dash rather than a confident zero.

Visualisations exist only where a picture answers a question faster than a number: the strike
layout strip (is this a straddle, a strangle, or a spread?), the schedule timeline (how long
until entry, how long is it held?), and utilisation meters.

## Accessibility

- Skip link to the workspace; `aria-current="page"` on the active navigation item.
- Single-choice controls are radio groups with arrow-key movement, not tab lists.
- The confirmation dialog traps focus, opens focused on the cancelling control, closes on
  Escape, and restores focus on unmount.
- Every icon-only control has a label; decorative glyphs are `aria-hidden`.
- Motion is confined to short entry transitions and is disabled under
  `prefers-reduced-motion`.
