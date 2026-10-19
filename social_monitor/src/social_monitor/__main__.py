import signal

from .cli import main

# Docker stop must unwind async generators so the upstream account lease is released.
signal.signal(signal.SIGTERM, signal.default_int_handler)

raise SystemExit(main())
