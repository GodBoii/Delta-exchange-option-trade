# Exit choices and contract expiry

Saved strategy templates contain legs, strike selection, order settings and risk controls. They do not contain an entry time, exit time or contract expiry. Each new scheduled run owns its resolved schedule and expiry. Older run snapshots keep their original definitions.

Both the agent calculator and manual schedule preview use the server's exit resolver. The choices are intraday 7 or 11 hours, overnight 16 or 24 hours, positional 48 or 72 hours, a specific timezone-aware exit timestamp, or the first or second eligible listed contract expiry. A 17:30 IST boundary separates options sessions. Intraday stays inside one session; overnight crosses one boundary; positional crosses at least two. A preset that conflicts with its label is rejected.

The expiry exit choices skip any contract that would leave less than 90 minutes from entry to the saved expiry buffer. For every other choice, the resolver chooses the earliest listed expiry whose settlement time minus the buffer covers the full requested exit. It checks that all legs and strike rules can resolve at that expiry and uses one expiry across the strategy. It never truncates a requested hold to fit a contract. A specific-time choice may cross any number of sessions, but still needs an eligible listed contract.

The preview returns entry and exit in UTC and IST, elapsed minutes, options-session crossings, the chosen contract settlement, and the latest buffered exit. Selection recomputes the schedule against the current saved template version and market snapshot. A manual schedule carries the preview's exit and contract expiry; the server rejects it if either changed. At activation, the engine resolves the live chain and checks the selected products' actual settlement before any order is sent.

Stops and profit targets can close a strategy earlier than the scheduled exit. These choices do not extend or modify an active trade. The proposed future monitoring rules are recorded in [future active-trade monitoring](26-future-active-trade-monitoring.md).

After building the matching writer and research images, run `python -m scripts.shared_library exit-templates` from `backend` to review the saved-library conversion. Run it again with `--apply` during the service handoff. The command refuses to convert while a future proposal awaits activation. It increments each changed template version once, leaves completed and open run snapshots alone, and retires the five duplicate next-day templates. Rerunning it makes no further changes.
