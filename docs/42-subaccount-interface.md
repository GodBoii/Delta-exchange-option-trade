# Sub account interface

## Analysis

The Next.js workspace uses `Tab` and `NAV_ITEMS` in `AppShell.tsx`, with availability
and lazy feature loading in `app/page.tsx`. Users already opens from the profile
dialog into the workspace. Sub account management previously mounted directly in
that dialog, including discovery, settings, transfers and the server IP poll.

`ProfileExchangeAccounts.tsx` owns the account controls. `lib/subaccounts.ts` validates
responses from the authenticated `/api/profile` endpoints. Account-scoped credentials,
assignment, funding safety and execution live in the Python backend. This change
preserves those API contracts and uses the existing global theme and motion tokens.

## Implementation plan

1. Add a profile-only Sub accounts destination alongside My P&L and Users.
2. Lazy-load its workspace view when the backend is online. Provide the connection
   action when the main account has not been connected.
3. Use compact account rows as the main content. Put account management, transfers,
   transfer history and API setup in secondary disclosures. Stack these below the
   accounts on smaller screens.
4. Reuse accordion recipe 21 and the existing page entrance. Keep closed controls
   inert, respect reduced motion, stop snapshot polling on collapse, and clear
   unsaved credentials when closing an account.
5. Verify navigation, settings, account details, key forms, transfer validation,
   loading/error/empty states, keyboard focus, themes and responsive overflow.
   Run frontend checks, commit each file separately and push main.

## Behavior

Profile contains a navigation entry instead of the manager. Opening Profile no
longer loads sub account discovery or server IP polling. Settings and funding are
collapsed initially. Unconfirmed transfers remain visible as a warning above the
account list even when activity is collapsed.

Live snapshots load only for expanded connected accounts. API key replacement,
removal and permanent run assignment remain behind further disclosure. All existing
trade confirmations, revision checks and transfer request IDs are retained.

## Verification

Browser checks used the real AppShell and account manager with an injected local
request fixture, at 390px and 1440px widths. Both themes rendered without horizontal
overflow. Checks covered profile navigation, keyboard expansion and collapse,
focus skipping inert panels, balances, positions and orders, management saves,
dummy API-key submission, transfer amount validation, server IP rendering,
unconfirmed transfer warnings, loading, empty, error/retry and disconnected states.
The installed reduced-motion media rule was confirmed in the browser stylesheet.
The temporary route was removed. No live funds, orders or credentials were changed.

The production build and TypeScript check pass. All 65 existing frontend tests
pass. ESLint passes for app, lib and tests. The
unrestricted lint command also scans the local Python virtual environment and fails
on third-party Matplotlib JavaScript there.
