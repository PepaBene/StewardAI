"""Read models that make the free-source data usable across holdings, portfolio and research.

Everything here composes existing read functions and the shared `free_source_reads` queries;
no figure is stored or recomputed outside deterministic code, unknown stays null, and every
claim carries the date it became known so a point-in-time cutoff can be honoured.
"""
