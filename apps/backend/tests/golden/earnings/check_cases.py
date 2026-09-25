"""Sanity checks for the earnings golden set.

Run from apps/backend:  uv run python tests/golden/earnings/check_cases.py

For every case directory: load case.json, decompress the archived document, convert it with
the same converter the extraction pipeline uses, and check that each figure's snippet is
found in that text, that value_as_written appears verbatim in the snippet, that figure keys
are unique, and that `value` is the plain reading of `value_as_written`.
"""

import gzip
import json
import re
import sys
from pathlib import Path

from steward.documents.text import contains, html_to_text

HERE = Path(__file__).resolve().parent
METRICS = {"revenue", "gross_profit", "operating_income", "net_income", "eps_diluted"}
BASES = {"reported", "adjusted"}
SCALES = {"units", "thousands", "millions", "billions"}
REQUIRED = [
    "id", "ticker", "company", "cik", "form", "source_url", "filing_index_url", "content_type",
    "document", "report_date", "fiscal_year_end_month", "reporting_currency", "period",
    "figures", "absent_metrics", "labelled_by", "notes",
]  # fmt: skip


def decode(raw: bytes) -> str:
    match = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", raw[:5000])
    if match:
        try:
            return raw.decode(match.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    return raw.decode("utf-8", "replace")


def check_case(case_dir: Path) -> list[str]:
    errors: list[str] = []
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    for key in REQUIRED:
        if key not in case:
            errors.append(f"missing key {key!r}")
    if case.get("id") != case_dir.name:
        errors.append(f"id {case.get('id')!r} != directory name {case_dir.name!r}")

    text = html_to_text(decode(gzip.decompress((case_dir / case["document"]).read_bytes())))
    fy = case["period"]["fiscal_year"]
    fp = case["period"]["fiscal_period"]

    seen: set[tuple] = set()
    for i, fig in enumerate(case["figures"]):
        period = f"{fig['fiscal_year']}{fig['fiscal_period']}"
        where = f"figure {i} ({fig['metric']}/{fig['basis']}/{period})"
        key = (fig["metric"], fig["basis"], fig["fiscal_year"], fig["fiscal_period"])
        if key in seen:
            errors.append(f"{where}: duplicate key {key}")
        seen.add(key)
        if fig["metric"] not in METRICS:
            errors.append(f"{where}: unknown metric")
        if fig["basis"] not in BASES:
            errors.append(f"{where}: unknown basis")
        if fig["basis"] == "adjusted" and fig["metric"] not in ("operating_income", "eps_diluted"):
            errors.append(f"{where}: adjusted basis only allowed for operating_income/eps_diluted")
        if fig["scale"] not in SCALES:
            errors.append(f"{where}: unknown scale")
        if fig["fiscal_period"] != fp or fig["fiscal_year"] not in (fy, fy - 1):
            errors.append(f"{where}: period is neither the case quarter nor the prior-year quarter")
        if fig["metric"] == "eps_diluted" and (fig["unit"], fig["scale"]) != ("per_share", "units"):
            errors.append(f"{where}: EPS must be unit=per_share, scale=units")
        if fig["metric"] != "eps_diluted" and fig["unit"] != "currency":
            errors.append(f"{where}: money metric must be unit=currency")
        if fig["metric"] in case.get("absent_metrics", []):
            errors.append(f"{where}: metric also listed in absent_metrics")
        if not contains(text, fig["snippet"]):
            errors.append(f"{where}: snippet not found in converted text")
        if fig["value_as_written"] not in fig["snippet"]:
            errors.append(f"{where}: value_as_written not in snippet")
        if fig.get("unit_snippet") and not contains(text, fig["unit_snippet"]):
            errors.append(f"{where}: unit_snippet not found in converted text")
        if float(fig["value_as_written"].replace(",", "")) != abs(fig["value"]):
            errors.append(
                f"{where}: value {fig['value']} != value_as_written {fig['value_as_written']!r}"
            )
        cc = fig.get("crosscheck")
        if cc is not None and cc.get("matches") is False:
            errors.append(f"{where}: XBRL crosscheck mismatch (value {cc.get('value')}); see notes")

    # every metric is either labelled (current + prior-year quarter) or declared absent
    for metric in METRICS - set(case.get("absent_metrics", [])):
        for year in (fy, fy - 1):
            if (metric, "reported", year, fp) not in seen:
                errors.append(f"missing reported {metric} for {year}{fp}")
    return errors


def main() -> int:
    case_dirs = sorted(p.parent for p in HERE.glob("*/case.json"))
    failed = 0
    figures = 0
    for case_dir in case_dirs:
        errors = check_case(case_dir)
        figures += len(json.loads((case_dir / "case.json").read_text(encoding="utf-8"))["figures"])
        status = "ok" if not errors else "FAIL"
        print(f"{status:4} {case_dir.name}")
        for error in errors:
            print(f"     - {error}")
        failed += bool(errors)
    print(f"{len(case_dirs)} cases, {figures} figures, {failed} failing")
    return 1 if failed or not case_dirs else 0


if __name__ == "__main__":
    sys.exit(main())
