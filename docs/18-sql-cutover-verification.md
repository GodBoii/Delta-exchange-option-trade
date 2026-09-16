# SQL verification, September 16, 2026

The user authorized direct execution of remaining SQL. Migration 021 was executed and committed on the production Supabase database through the existing Ubuntu analysis container's PostgreSQL connection. No secrets were printed.

Before execution, live checks found no public-table INSERT, UPDATE or DELETE grants for anon/authenticated, and neither library foreign key removed by 020 remained. Consequently, 021 repeated existing restrictions rather than removing currently available client write privileges.

The verification transaction used a five-second lock timeout, a thirty-second statement timeout and brief share locks on the eleven affected tables. Counts before and after matched: profiles 4, exchange_connections 1, strategies 16, executions 33, execution_orders 71, strategy_capital_slots 1, strategy_proposals 14, automation_settings 1, automation_agent_runs 113, automation_market_snapshots 101, capital_settings 4.

No targeted client mutation grants remain. Service-role table grants and the Auth/Agno table inventory were unchanged. This migration contains only permission revocations, with no data updates or deletion.

This verifies the SQL permission change, not completion of the Convex data migration. Shared-analysis integration and the final application cutover remain separate work. Older migrations were not blindly replayed because they include historical data changes.
