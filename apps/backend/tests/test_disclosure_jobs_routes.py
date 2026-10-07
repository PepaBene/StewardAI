"""The ingest and identifier jobs are reachable only as owner routes."""

from api.main import create_app


def test_job_routes_are_registered_as_post_only() -> None:
    paths = create_app().openapi()["paths"]
    for path in ("/disclosures/issuers/{issuer_id}/ingest", "/disclosures/identifiers/resolve"):
        assert list(paths[path]) == ["post"]
