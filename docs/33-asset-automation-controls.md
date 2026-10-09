# BTC and ETH automation controls

The owner can pause or resume each asset on the Automation page. These controls apply
to shared analysis and automated entries for every account. The existing account
participation switch remains independent.

`PUT /api/automation/assets/BTC` or `/ETH` accepts `{"enabled": false}` or `true`.
The backend verifies the owner role and the configured system owner. The overview
returns `assetAutomation` with the effective state of both assets.

The settings live in `trade.system_settings.analysis.asset_enabled`. Missing flags
mean enabled, preserving existing installations. The existing global `enabled` flag
continues to override both assets. No database migration is needed.

Pausing locks the system settings and cancels that asset's scheduled and running
analysis jobs, scheduled proposals and their scheduled account strategies in one
transaction. It broadcasts an automation revision to connected browsers. Writes use
the existing runtime store to maintain indexed state, reporting and capital releases.
Active or executing strategies and manually scheduled strategies remain unchanged.

Claiming analysis, creating future sessions, committing research actions and allocating
shared decisions acquire a shared settings lock before job or proposal locks. They
check the asset flag again, protecting against stale scheduler reads and late results.
An AI request already sent may finish at its provider, but its cancelled run cannot
commit another action, even if the owner resumes the asset before it responds.

Resuming allows future fixed sessions to be scheduled again. It does not restore
cancelled proposals or entries.

Verification covered desktop and mobile rendering, keyboard resume, persisted display
after reload, failed saves, owner authorization, legacy BTC records, per-asset
cancellation and interrupted analysis writes. Browser interaction checks used mocked
API responses. PostgreSQL tests used a disposable database on Ubuntu, without production
credentials or exchange calls. The broader database check found an existing failure in
`test_library_save_revision_and_soft_delete`, reproduced using the unchanged test image.
That fixture lacks fields now required by strategy validation.
