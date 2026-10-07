"""Portfolio X-Ray (plan Phase 5): look-through views built from dated fund and direct holdings.

The package is split so each part can be tested without a database:

- `leaves`: every direct holding and every published fund row becomes one weighted leaf that
  keeps its channel (direct, or which fund) and how each classification was obtained.
- `views`: selectable X-Ray dimensions; every view adds up to the whole portfolio.
- `metrics`: concentration at several levels, weighted fundamentals, fee drag.
- `intersection`: stock intersection across channels and the effect of removing a fund.
- `benchmark`: benchmark-relative comparison, like-for-like only.
- `scenario`: a proposed portfolio built on a copy; saved holdings are never touched.
- `service`: the database-facing assembly.
"""
