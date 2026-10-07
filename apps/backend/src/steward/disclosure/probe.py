"""Access probes for the public-disclosure sources (plan P0.4).

    uv run python -m steward.disclosure.probe [--json]

Makes a handful of real, polite requests (one to three per source, declared User-Agent, 0.5 s
apart) and prints what each source actually answered: status, size, validators, and the
`SourceAccess` verdict. It writes nothing to any database; the owner records a verdict with
`record_access_test` after reading the table. Credentials come from the environment/.env and are
never printed. A probe that cannot test a source (no key, browser-only) says so instead of
guessing, and the source stays `untested`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from steward.disclosure.fetch import classify_failure
from steward.disclosure.registry import SourceAccess
from steward.settings import Settings, get_settings

APPLE_CIK = "0000320193"
APPLE_LEI = "HWUPKR0MPOU8FGXBT394"
PAUSE = 0.5


@dataclass(slots=True)
class ProbeResult:
    source_key: str
    verdict: SourceAccess
    detail: str
    status_code: int | None = None
    bytes: int | None = None
    facts: dict[str, Any] = field(default_factory=dict)


Probe = Callable[[httpx.AsyncClient, Settings], Awaitable[list[ProbeResult]]]


def verdict_for(status: int, body: str = "") -> SourceAccess:
    if status == 200:
        return SourceAccess.AVAILABLE
    state, _ = classify_failure(status, body)
    return SourceAccess(state if state != "not_covered" else "not_covered")


async def _get(
    http: httpx.AsyncClient, url: str, **kwargs: Any
) -> tuple[httpx.Response | None, str | None]:
    await asyncio.sleep(PAUSE)
    try:
        return await http.get(url, follow_redirects=True, **kwargs), None
    except httpx.HTTPError as exc:
        return None, type(exc).__name__


def _failed(source_key: str, error: str) -> ProbeResult:
    return ProbeResult(source_key, SourceAccess.RETRIEVAL_FAILED, f"request failed: {error}")


async def probe_sec(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    agent = settings.sec_user_agent
    if not agent or "@" not in agent:
        return [
            ProbeResult(
                k, SourceAccess.UNTESTED, "SEC_USER_AGENT with a contact address is not set"
            )
            for k in ("sec.submissions", "sec.archive", "sec.companyfacts")
        ]
    headers = {"User-Agent": agent, "Accept-Encoding": "gzip, deflate"}
    out: list[ProbeResult] = []
    sub, err = await _get(
        http, f"https://data.sec.gov/submissions/CIK{APPLE_CIK}.json", headers=headers
    )
    if sub is None:
        return [_failed("sec.submissions", err or "")]
    facts: dict[str, Any] = {
        "etag": sub.headers.get("ETag"),
        "last_modified": sub.headers.get("Last-Modified"),
    }
    if sub.status_code == 200:
        recent = sub.json().get("filings", {}).get("recent", {})
        facts["recent_filings"] = len(recent.get("form", []))
        facts["has_acceptance_time"] = bool(recent.get("acceptanceDateTime"))
        validators = {
            k: v
            for k, v in (
                ("If-None-Match", facts["etag"]),
                ("If-Modified-Since", facts["last_modified"]),
            )
            if v
        }
        if validators:
            again, _ = await _get(
                http,
                f"https://data.sec.gov/submissions/CIK{APPLE_CIK}.json",
                headers={**headers, **validators},
            )
            facts["conditional_fetch_status"] = again.status_code if again is not None else None
    out.append(
        ProbeResult(
            "sec.submissions",
            verdict_for(sub.status_code),
            f"HTTP {sub.status_code}",
            sub.status_code,
            len(sub.content),
            facts,
        )
    )
    index, err = await _get(
        http,
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/index.json",
        headers=headers,
    )
    out.append(
        _failed("sec.archive", err or "")
        if index is None
        else ProbeResult(
            "sec.archive",
            verdict_for(index.status_code),
            f"archive index.json HTTP {index.status_code}",
            index.status_code,
            len(index.content),
        )
    )
    cf, err = await _get(
        http, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{APPLE_CIK}.json", headers=headers
    )
    out.append(
        _failed("sec.companyfacts", err or "")
        if cf is None
        else ProbeResult(
            "sec.companyfacts",
            verdict_for(cf.status_code),
            f"HTTP {cf.status_code}",
            cf.status_code,
            len(cf.content),
        )
    )
    for key, url in (
        (
            "sec.fsn_bulk",
            "https://www.sec.gov/files/dera/data/financial-statement-and-notes-data-set/2025q2_notes.zip",
        ),
        (
            "sec.nport_bulk",
            "https://www.sec.gov/files/dera/data/form-n-port-data-sets/2025q3_nport.zip",
        ),
    ):
        resp, err = await _get(http, url, headers={**headers, "Range": "bytes=0-1023"})
        out.append(
            _failed(key, err or "")
            if resp is None
            else ProbeResult(
                key,
                SourceAccess.AVAILABLE
                if resp.status_code in (200, 206)
                else verdict_for(resp.status_code),
                f"ranged GET HTTP {resp.status_code} "
                "(dataset name is a guess; check the SEC data-sets page)",
                resp.status_code,
                len(resp.content),
                {"accept_ranges": resp.headers.get("Accept-Ranges")},
            )
        )
    return out


async def probe_esef(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    resp, err = await _get(http, "https://filings.xbrl.org/api/filings", params={"page[size]": 1})
    if resp is None:
        return [_failed("esef.filings_xbrl_org", err or "")]
    return [
        ProbeResult(
            "esef.filings_xbrl_org",
            verdict_for(resp.status_code),
            f"HTTP {resp.status_code}",
            resp.status_code,
            len(resp.content),
        )
    ]


async def probe_fca(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    await asyncio.sleep(PAUSE)
    try:
        resp = await http.post(
            "https://api.data.fca.org.uk/search?index=fca-nsm-searchdata",
            json={
                "from": 0,
                "size": 1,
                "sort": "submitted_date",
                "sortorder": "desc",
                "criteriaObj": {"criteria": [], "dateCriteria": []},
            },
        )
    except httpx.HTTPError as exc:
        return [_failed("fca.nsm", type(exc).__name__)]
    detail = f"public search POST HTTP {resp.status_code}"
    verdict = verdict_for(resp.status_code, resp.text[:500])
    return [
        ProbeResult(
            "fca.nsm",
            verdict,
            detail,
            resp.status_code,
            len(resp.content),
            {"note": "read route is the NSM search backend, not the PIP submission API"},
        )
    ]


async def probe_companies_house(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    key = settings.companies_house_api_key
    if key is None:
        return [
            ProbeResult(
                "companies_house.api", SourceAccess.UNTESTED, "COMPANIES_HOUSE_API_KEY is not set"
            )
        ]
    resp, err = await _get(
        http,
        "https://api.company-information.service.gov.uk/company/00000006",
        auth=httpx.BasicAuth(key.get_secret_value(), ""),
    )
    if resp is None:
        return [_failed("companies_house.api", err or "")]
    facts = {"rate_limit_remaining": resp.headers.get("X-Ratelimit-Remain")}
    verdict = (
        SourceAccess.AUTHENTICATION_FAILED
        if resp.status_code in (401, 403)
        else verdict_for(resp.status_code)
    )
    return [
        ProbeResult(
            "companies_house.api",
            verdict,
            f"HTTP {resp.status_code}",
            resp.status_code,
            len(resp.content),
            facts,
        )
    ]


async def probe_edinet(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    key = settings.edinet_api_key
    if key is None:
        return [
            ProbeResult(
                "edinet.api",
                SourceAccess.UNTESTED,
                "EDINET_API_KEY is not set; register for a free key, then re-run",
            )
        ]
    resp, err = await _get(
        http,
        "https://api.edinet-fsa.go.jp/api/v2/documents.json",
        params={
            "date": (dt.date.today() - dt.timedelta(days=3)).isoformat(),
            "type": 1,
            "Subscription-Key": key.get_secret_value(),
        },
    )
    if resp is None:
        return [_failed("edinet.api", err or "")]
    verdict = (
        SourceAccess.AUTHENTICATION_FAILED
        if resp.status_code in (401, 403)
        else verdict_for(resp.status_code)
    )
    return [
        ProbeResult(
            "edinet.api", verdict, f"HTTP {resp.status_code}", resp.status_code, len(resp.content)
        )
    ]


async def probe_ted(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    await asyncio.sleep(PAUSE)
    try:
        resp = await http.post(
            "https://api.ted.europa.eu/v3/notices/search",
            json={"query": "buyer-country=CZE", "fields": ["notice-title"], "limit": 1, "page": 1},
        )
    except httpx.HTTPError as exc:
        return [_failed("ted.search", type(exc).__name__)]
    return [
        ProbeResult(
            "ted.search",
            verdict_for(resp.status_code, resp.text[:500]),
            f"anonymous search POST HTTP {resp.status_code}",
            resp.status_code,
            len(resp.content),
        )
    ]


async def probe_gleif(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    resp, err = await _get(
        http, f"https://api.gleif.org/api/v1/lei-records/{APPLE_LEI}/direct-parent-relationship"
    )
    if resp is None:
        return [_failed("gleif.level2", err or "")]
    note = {"note": "404 here means 'no reported parent', which is itself a Level 2 answer"}
    verdict = (
        SourceAccess.AVAILABLE if resp.status_code in (200, 404) else verdict_for(resp.status_code)
    )
    return [
        ProbeResult(
            "gleif.level2",
            verdict,
            f"HTTP {resp.status_code}",
            resp.status_code,
            len(resp.content),
            note,
        )
    ]


async def probe_sedar(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    resp, err = await _get(http, "https://www.sedarplus.ca/")
    if resp is None:
        return [_failed("sedarplus.portal", err or "")]
    return [
        ProbeResult(
            "sedarplus.portal",
            SourceAccess.UNTESTED,
            f"portal reachable (HTTP {resp.status_code}); search/export, stable document IDs and "
            "incremental polling need a browser session and are NOT proven by this probe",
            resp.status_code,
            len(resp.content),
        )
    ]


async def probe_issuer_ir(http: httpx.AsyncClient, settings: Settings) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    for name, url in (
        ("shell", "https://www.shell.com/investors/results-and-reporting/consensus-estimates.html"),
        ("lseg", "https://www.lseg.com/en/investor-relations/consensus"),
    ):
        resp, err = await _get(
            http, url, headers={"User-Agent": settings.sec_user_agent or "steward-probe"}
        )
        if resp is None:
            out.append(_failed("issuer_ir.consensus", f"{name}: {err}"))
            continue
        files = re.findall(r'href="([^"]+\.(?:xlsx?|pdf))"', resp.text, re.IGNORECASE)
        out.append(
            ProbeResult(
                "issuer_ir.consensus",
                (SourceAccess.AVAILABLE if files else SourceAccess.NOT_COVERED)
                if resp.status_code == 200
                else verdict_for(resp.status_code),
                f"{name}: HTTP {resp.status_code}, {len(files)} xls/pdf links"
                + ("" if files else " (no versioned file to fetch)"),
                resp.status_code,
                len(resp.content),
                {
                    "issuer": name,
                    "last_modified": resp.headers.get("Last-Modified"),
                    "sample": files[:3],
                },
            )
        )
    return out


PROBES: tuple[Probe, ...] = (
    probe_sec,
    probe_esef,
    probe_fca,
    probe_companies_house,
    probe_edinet,
    probe_ted,
    probe_gleif,
    probe_sedar,
    probe_issuer_ir,
)


async def run_probes(settings: Settings | None = None) -> list[ProbeResult]:
    settings = settings or get_settings()
    agent = settings.sec_user_agent or "steward-probe"
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": agent}) as http:
        results: list[ProbeResult] = []
        for probe in PROBES:
            results.extend(await probe(http, settings))
        return results


def render(results: list[ProbeResult]) -> str:
    today = dt.datetime.now(dt.UTC).date().isoformat()
    lines = [
        f"Probed {today} (UTC). Verdicts are observations, not enablement.",
        "",
        "| source | verdict | status | bytes | detail |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        facts = "; ".join(f"{k}={v}" for k, v in r.facts.items() if v not in (None, "", []))
        detail = f"{r.detail} [{facts}]" if facts else r.detail
        lines.append(
            f"| {r.source_key} | {r.verdict.value} | {r.status_code or ''} | {r.bytes or ''} "
            f"| {detail} |"
        )
    return "\n".join(lines)


def main() -> None:
    results = asyncio.run(run_probes())
    if "--json" in sys.argv:
        print(
            json.dumps(
                [{**asdict(r), "verdict": r.verdict.value} for r in results], indent=2, default=str
            )
        )
    else:
        print(render(results))


if __name__ == "__main__":
    main()
