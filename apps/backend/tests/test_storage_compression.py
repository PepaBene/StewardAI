import gzip
import time

import httpx
import pytest

from steward.storage import (
    SupabaseStorage,
    is_compressible,
    sign_blob,
    unpack,
    verify_blob,
)


def test_text_types_are_compressible_and_binaries_are_not() -> None:
    assert is_compressible("text/html; charset=utf-8")
    assert is_compressible("application/json")
    assert is_compressible("application/vnd.api+json")
    assert not is_compressible("application/pdf")
    assert not is_compressible("image/png")


def test_unpack_reads_both_gzip_and_plain_objects() -> None:
    body = b"<html>" + b"x" * 1000 + b"</html>"
    assert unpack(gzip.compress(body)) == body
    assert unpack(body) == body


def test_blob_signature_binds_path_and_expiry() -> None:
    exp = int(time.time()) + 60
    sig = sign_blob("secret", "ab/abc.html", exp)
    assert verify_blob("secret", "ab/abc.html", exp, sig)
    assert not verify_blob("secret", "ab/other.html", exp, sig)
    assert not verify_blob("other", "ab/abc.html", exp, sig)
    assert not verify_blob("secret", "ab/abc.html", exp + 1, sig)
    old = int(time.time()) - 1
    assert not verify_blob("secret", "ab/abc.html", old, sign_blob("secret", "ab/abc.html", old))


@pytest.mark.asyncio
async def test_put_gzips_text_only_when_a_public_url_is_set_and_get_restores_it() -> None:
    stored: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        if request.method == "POST":
            stored[key] = request.content
            return httpx.Response(200, json={})
        return httpx.Response(200, content=stored[key])

    page = b"<html>" + b"filing text " * 5000 + b"</html>"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        on = SupabaseStorage(
            http, "https://x.supabase.co", "sb_key", public_api_url="https://api.x"
        )
        off = SupabaseStorage(http, "https://x.supabase.co", "sb_key")
        await on.put("aa/a.html", page, "text/html")
        await off.put("bb/b.html", page, "text/html")
        await on.put("cc/c.pdf", b"%PDF" + b"0" * 500, "application/pdf")
        assert len(stored["/storage/v1/object/evidence/aa/a.html"]) < len(page) / 5
        assert stored["/storage/v1/object/evidence/bb/b.html"] == page
        assert stored["/storage/v1/object/evidence/cc/c.pdf"].startswith(b"%PDF")
        assert await on.get("aa/a.html") == page
        assert await off.get("aa/a.html") == page
        url = await on.signed_url("aa/a.html", 600)
        assert url.startswith("https://api.x/evidence/blob?path=aa%2Fa.html&exp=")
