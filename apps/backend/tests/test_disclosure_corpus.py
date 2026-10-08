"""P0.2 / P0.3: the pilot corpus and sample-capture manifests keep the composition the plan asks
for, and never claim a capture that has not happened."""

import json
from collections import Counter
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "disclosure"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_corpus_has_45_unique_issuers_in_the_required_pools() -> None:
    corpus = load("corpus.json")
    issuers = corpus["issuers"]
    assert len(issuers) == 45
    assert len({i["key"] for i in issuers}) == 45
    assert len({i["name"] for i in issuers}) == 45
    pools = Counter(i["pool"] for i in issuers)
    for pool, minimum in corpus["quotas"].items():
        assert pools[pool] >= minimum, pool


def test_corpus_covers_every_required_edge_case() -> None:
    corpus = load("corpus.json")
    roles = {r for i in corpus["issuers"] for r in i["roles"]}
    # an ADR is represented by its issuer entry (see adr_note)
    covered = roles | ({"adr"} if "adr_issuer" in roles else set())
    assert set(corpus["required_roles"]) <= covered


def test_identifiers_are_never_invented() -> None:
    for issuer in load("corpus.json")["issuers"]:
        if issuer["cik"] is not None:
            assert issuer["pool"] == "us" and len(issuer["cik"]) == 10 and issuer["cik"].isdigit()
        else:
            assert issuer.get("resolve_via"), issuer["key"]
        assert not {"lei", "isin", "figi"} & issuer.keys()  # filled only after verification


def test_sample_manifest_is_honest_about_what_was_captured() -> None:
    manifest = load("samples_manifest.json")
    slots = [s for group in manifest["slots"].values() for s in group]
    # `captured` means a person has read the slot; the model's own check never counts as that
    assert manifest["captured"] == sum(1 for s in slots if s["status"] == "captured") == 0
    assert manifest["captured_model_checked"] == sum(
        1 for s in slots if s["status"] == "captured_model_checked"
    )
    for s in slots:
        assert s["status"] in manifest["status_values"], s["slot"]
        if s["status"] == "captured_model_checked":
            assert s["sha256"] and s["expected"] and s["captured_by"].startswith("claude-model")
        if s["status"] == "rejected_unavailable":
            assert s["reason"], s["slot"]
    assert manifest["samples_per_category"] == 10
    assert len(manifest["categories"]) == 11  # Form 4 purchase/sale and non-discretionary split
    for name, spec in manifest["categories"].items():
        assert spec["expected_fields"] and spec["channel"], name
