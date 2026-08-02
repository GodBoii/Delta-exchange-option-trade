# Delta Strategy Desk

A client-facing strategy workstation for Delta Exchange India. Supabase Auth provides persistent email/password and Google sign-in, Supabase Postgres stores user-scoped strategies and executions, and Supabase Vault protects each user's one-time Delta API connection.

## Security first

- Never place Delta API secrets in source code, browser storage, screenshots, or Git.
- Rotate any key that has been pasted into chat or shared in an image before using this app.
- Users can sign in or create an account with email/password through Supabase Auth. Google OAuth is available alongside it when the provider is configured. Delta credentials are entered once after application authentication, validated against `GET /v2/profile`, and stored through a server-only Supabase Vault function.
- Delta signatures are generated server-side immediately before each request. The secret is never returned to the browser.
- The Supabase service-role key is server-only and must never use a `NEXT_PUBLIC_` name.
- Use a dedicated API key with only the permissions needed. Trading keys require the deployed server's static public IP to be allowlisted in Delta.
- Start with Delta testnet. Production orders can lose money.

## Run locally

Requirements: Node.js 22+.

1. Install dependencies:

   ```powershell
   npm install
   ```

2. Run the complete database migration in Supabase SQL Editor:

   [`supabase/migrations/001_initial_schema.sql`](supabase/migrations/001_initial_schema.sql)

3. Create `.env.local` from `.env.example`. Add the Supabase project URL, publishable key, and the server-only service-role key.

4. Configure Google under Supabase Dashboard > Authentication > Sign In / Providers. The Google OAuth callback URI is:

   `https://xphxxkmeqqgjobkmclso.supabase.co/auth/v1/callback`

5. Start the web app:

   ```powershell
   npm run dev
   ```

6. In a second terminal, start the durable scheduler:

   ```powershell
   npm run worker
   ```

The worker must remain online for scheduled entry and exit. For deployment, run the web process and worker as separate supervised services using the same Supabase project and server credentials.

## Strategy execution model

Delta's batch-order endpoint only supports orders belonging to one contract. Option legs have different product IDs, so a multi-leg strategy cannot be exchange-atomic. Delta Strategy Desk therefore:

1. Resolves every leg from the live option chain and presents a preview.
2. Requires an explicit real-funds acknowledgement.
3. Assigns an idempotent client order ID to each leg.
4. Submits legs sequentially and stops after the first failure.
5. Records successful and failed submissions for reconciliation.
6. Uses reduce-only market orders for the scheduled strategy exit.

Per-leg target, stop-loss, and trailing-stop values are converted from points around the preview mark and attached as Delta bracket parameters. Market slippage means the actual distance from the fill can differ from the preview.

Advanced cross-leg lifecycle fields (overall target/stop, break-even propagation, and automatic re-entry counts) are persisted and displayed in previews, but this version does not automatically monitor or execute them. Automating them safely requires strategy-scoped fill attribution; Delta exposes net product positions, so treating a shared product position as belonging to one strategy could close or reopen unrelated trades. The preview calls out this limitation whenever one of these fields is configured.

## Commands

```powershell
npm test
npm run typecheck
npm run lint
npm run build
```

## Important operational notes

- System time must be synchronized. Delta rejects signatures older than five seconds.
- Run one scheduler worker initially. Before horizontally scaling workers, add a database-backed distributed lease/claim RPC.
- Keep Row Level Security enabled. Delta connections and Vault RPCs remain inaccessible to browser roles.
- This project calls Delta's REST API directly because exact signing, India/testnet host selection, and product-specific option resolution are core requirements. CCXT and `delta-rest-client` are not required at runtime.
- The public WebSocket migration described in Delta's 2026 changelog is not needed for this REST-based version. A future real-time market tape should use `wss://public-socket.india.delta.exchange`, not deprecated legacy public channels.

## Reference

- [Delta Exchange API documentation](https://docs.delta.exchange/)
- [Delta REST client on PyPI](https://pypi.org/project/delta-rest-client/)
- [CCXT](https://github.com/ccxt/ccxt)
