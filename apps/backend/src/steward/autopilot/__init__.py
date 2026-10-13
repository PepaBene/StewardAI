"""The autopilot: the app settles its own review queues and the owner sees what it could not.

`ledger` records every decision, `judge` is the loop that makes them, `calibration` decides
which kinds may act alone, `kinds` holds one module per queue, and `job` wires it all into the
worker. See docs/plans/autonomy_plan.md.
"""
