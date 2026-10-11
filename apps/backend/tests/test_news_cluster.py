"""Dedupe and clustering.

Phase 3 AC: 10 known syndicated-copy test cases (same wire story, different outlets) cluster
into one story_cluster each. The fixture also holds distractors (other stories about the same
company, same wording about another company) that must stay out of those clusters.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from steward.news.cluster import ClusterMember, Fingerprint, best_cluster, same_story
from steward.news.normalize import canonical_url, headline_key, strip_outlet

CASES = json.loads((Path(__file__).parent / "news" / "syndication_cases.json").read_text())


def _cluster_all(items: list[tuple[dict[str, Any], str]]) -> dict[int, list[str]]:
    """Assign items to clusters in publication order, as the pipeline does."""
    members: list[ClusterMember] = []
    clusters: dict[int, list[str]] = {}
    for item, label in sorted(items, key=lambda x: x[0]["published_at"]):
        fp = Fingerprint.of(item["headline"], item["summary"], (item.get("outlet_name") or "",))
        cluster = best_cluster(fp, members)
        if cluster is None:
            cluster = len(clusters) + 1
        members.append(ClusterMember(cluster, fp))
        clusters.setdefault(cluster, []).append(label)
    return clusters


def test_fixture_shape() -> None:
    assert len(CASES["cases"]) == 10
    assert all(len(c["copies"]) >= 2 for c in CASES["cases"])
    urls = [c["url"] for case in CASES["cases"] for c in case["copies"]]
    urls += [d["url"] for d in CASES["distractors"]]
    assert len(urls) == len(set(urls))


def test_each_syndicated_story_forms_exactly_one_cluster() -> None:
    items = [(copy, case["id"]) for case in CASES["cases"] for copy in case["copies"]]
    items += [(d, f"distractor:{d['url']}") for d in CASES["distractors"]]
    clusters = _cluster_all(items)

    for case in CASES["cases"]:
        holding = [cid for cid, labels in clusters.items() if case["id"] in labels]
        assert len(holding) == 1, f"{case['id']} split across clusters {holding}"
        labels = clusters[holding[0]]
        assert set(labels) == {case["id"]}, f"{case['id']} merged with {set(labels)}"
        assert len(labels) == len(case["copies"])


@pytest.mark.parametrize("distractor", CASES["distractors"], ids=lambda d: d["about_case"])
def test_distractor_is_not_a_copy(distractor: dict[str, Any]) -> None:
    case = next(c for c in CASES["cases"] if c["id"] == distractor["about_case"])
    d = Fingerprint.of(distractor["headline"], distractor["summary"])
    for copy in case["copies"]:
        assert not same_story(d, Fingerprint.of(copy["headline"], copy["summary"]))


def test_outlet_suffixes_and_wire_prefixes_are_ignored() -> None:
    assert strip_outlet("UPDATE 2-Apple buys startup - Reuters") == "Apple buys startup"
    assert strip_outlet("Apple buys startup (Reuters)") == "Apple buys startup"
    assert strip_outlet("Apple buys startup | CNBC") == "Apple buys startup"
    # A dash that is part of the headline stays.
    assert strip_outlet("Apple buys startup - shares rise") == "Apple buys startup - shares rise"
    assert headline_key("Apple to buy X for $2bln") == headline_key("Apple to buy X for $2 billion")


def test_canonical_url_rejects_non_http_schemes_and_bad_ports() -> None:
    for bad in ("javascript:alert(1)", "ftp://x.test/a", "https://x.test:notaport/a"):
        with pytest.raises(ValueError):
            canonical_url(bad)


def test_canonical_url_drops_tracking_and_fragments() -> None:
    a = canonical_url("https://WWW.Reuters.com/tech/apple/?utm_source=rss&utm_medium=x#top")
    b = canonical_url("https://www.reuters.com/tech/apple")
    assert a == b
    # A query that identifies the article is kept.
    assert canonical_url("https://finnhub.io/api/news?id=abc") != canonical_url(
        "https://finnhub.io/api/news?id=def"
    )
