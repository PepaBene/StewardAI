"""CSV of the X-Ray lenses (plan M5.1): one row per bucket of every lens, with how it was
classified and its largest contributors."""

from __future__ import annotations

from steward.export import to_csv
from steward.xray.views import XrayView

HEADER = [
    "lens", "bucket", "kind", "weight", "value_base", "direct_weight", "fund_weight",
    "reported", "inferred", "owner", "contributors",
]  # fmt: skip


def xray_csv(views: list[XrayView], lens: str | None = None) -> str:
    chosen = [v for v in views if lens is None or v.dimension == lens]
    rows = []
    for v in chosen:
        for b in v.buckets:
            contributors = "; ".join(f"{c.label} {c.weight:.4%}" for c in b.contributors)
            rows.append(
                [v.dimension, b.label, b.kind, b.weight, b.value_base, b.direct_weight,
                 b.fund_weight, b.basis.get("reported", 0.0), b.basis.get("inferred", 0.0),
                 b.basis.get("owner", 0.0), contributors]
            )  # fmt: skip
    return to_csv(HEADER, rows)
