"""The evidence archive: Supabase Storage bucket `evidence`, via the Storage REST API.

Objects are keyed by the SHA-256 of their bytes (`ab/abcdef....html`), so the same page
fetched twice is stored once and a stored figure always points at the exact bytes it was
read from, even after the website changes. The bucket is private; the frontend gets
short-lived signed URLs from the API.
"""

import urllib.parse
from typing import Protocol

import httpx

BUCKET = "evidence"


class StorageError(Exception):
    pass


class EvidenceStore(Protocol):
    async def put(self, path: str, data: bytes, content_type: str) -> None: ...

    async def get(self, path: str) -> bytes: ...

    async def signed_url(self, path: str, expires_in: int) -> str: ...


def evidence_path(sha256: str, extension: str) -> str:
    return f"{sha256[:2]}/{sha256}.{extension}"


class SupabaseStorage:
    def __init__(self, http: httpx.AsyncClient, supabase_url: str, secret_key: str) -> None:
        self._http = http
        self._base = f"{supabase_url.rstrip('/')}/storage/v1"
        # New secret keys (sb_secret_...) are not JWTs: they go in `apikey` only. Legacy
        # service_role keys are JWTs and go in both headers.
        self._headers = {"apikey": secret_key}
        if not secret_key.startswith("sb_"):
            self._headers["Authorization"] = f"Bearer {secret_key}"

    def _object(self, path: str) -> str:
        return f"{BUCKET}/{urllib.parse.quote(path)}"

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        response = await self._http.post(
            f"{self._base}/object/{self._object(path)}",
            content=data,
            headers={**self._headers, "Content-Type": content_type, "x-upsert": "false"},
        )
        if response.status_code in (200, 201):
            return
        # Same bytes, same key: already archived.
        if response.status_code in (400, 409) and "exists" in response.text.lower():
            return
        raise StorageError(f"storage upload failed: HTTP {response.status_code} {response.text}")

    async def get(self, path: str) -> bytes:
        response = await self._http.get(
            f"{self._base}/object/{self._object(path)}", headers=self._headers
        )
        if response.status_code != 200:
            raise StorageError(f"storage download failed: HTTP {response.status_code}")
        return response.content

    async def signed_url(self, path: str, expires_in: int) -> str:
        response = await self._http.post(
            f"{self._base}/object/sign/{self._object(path)}",
            json={"expiresIn": expires_in},
            headers=self._headers,
        )
        if response.status_code != 200:
            raise StorageError(f"signing failed: HTTP {response.status_code} {response.text}")
        signed = response.json().get("signedURL") or response.json().get("signedUrl")
        if not isinstance(signed, str):
            raise StorageError("signing response has no signedURL")
        return f"{self._base}{signed}" if signed.startswith("/") else signed


class MemoryStorage:
    """In-process store for tests and the golden-set run."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        self.objects.setdefault(path, (data, content_type))

    async def get(self, path: str) -> bytes:
        if path not in self.objects:
            raise StorageError(f"no object {path}")
        return self.objects[path][0]

    async def signed_url(self, path: str, expires_in: int) -> str:
        if path not in self.objects:
            raise StorageError(f"no object {path}")
        return f"memory://{BUCKET}/{path}?expires_in={expires_in}"
