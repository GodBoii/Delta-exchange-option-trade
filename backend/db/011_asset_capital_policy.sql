-- Preserve complete allocation settings for owner edits and policy history.
-- Existing rows and scalar reporting fields remain readable.
alter table owner_reporting.capital_policy_changes
    add column if not exists old_policy_json jsonb,
    add column if not exists new_policy_json jsonb;
alter table owner_reporting.capital_observations
    add column if not exists policy_json jsonb;
