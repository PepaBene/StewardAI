"""Valuation sets drafted for the owner are not the owner's own valuation.

On 7 Oct 2026 every held company got a "starting set": the default DCF from the company's filed
growth, saved under the owner's id with a note that says it was drafted. Read as the owner's
saved set, it headlined fair values as "Your DCF scenario" and theses cited it as the owner's
valuation to argue an exit (Netflix, Cisco, AstraZeneca, Meta). A drafted set stays on the
valuation tab to edit; once the owner saves a version of their own (a new row without this
note), that version is theirs and counts.
"""

DRAFTED_PREFIX = "Starting set drafted"

# A saved-set row the owner wrote (for `company_valuation_scenario`).
OWNED_SET_SQL = "coalesce(note, '') not like 'Starting set drafted%'"

# A fair-value point that does not come from a drafted set (for `fair_value_point`).
OWNED_POINT_SQL = (
    "(scenario_id is null or scenario_id not in (select id from"
    " public.company_valuation_scenario where note like 'Starting set drafted%'))"
)


def is_drafted(note: str | None) -> bool:
    return (note or "").startswith(DRAFTED_PREFIX)
