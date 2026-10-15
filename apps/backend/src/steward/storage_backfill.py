"""One-off: gzip the text objects already in the evidence bucket.

    python -m steward.storage_backfill            # report what would shrink
    python -m steward.storage_backfill --apply    # rewrite the objects in place

Needs PUBLIC_API_URL set (links to compressed objects go through /evidence/blob) and the same
database and Supabase settings as the backend. Safe to repeat: gzip objects are skipped, and every
reader unpacks by the gzip header, so old and new objects read the same way.
"""

import argparse
import asyncio
import mimetypes

import httpx

from steward.db import create_pool
from steward.settings import get_settings
from steward.storage import StorageError, SupabaseStorage

_PATHS = (
    "select archive_path as path from public.source_document_version where archive_path is not null"
    " union select storage_path from public.evidence where storage_path is not null"
)


async def run(apply: bool) -> int:
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_secret_key and settings.public_api_url):
        print("SUPABASE_URL, SUPABASE_SECRET_KEY and PUBLIC_API_URL must all be set")
        return 1
    pool = await create_pool(settings)
    before = after = done = failed = 0
    try:
        paths = [r["path"] for r in await pool.fetch(_PATHS)]
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as http:
            storage = SupabaseStorage(
                http,
                settings.supabase_url,
                settings.supabase_secret_key.get_secret_value(),
                public_api_url=settings.public_api_url,
            )
            for path in paths:
                content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                try:
                    if apply:
                        result = await storage.recompress(path, content_type)
                    else:
                        textual = content_type.startswith(("text/", "application/"))
                        result = (0, 0) if textual else None
                except StorageError as exc:
                    failed += 1
                    print(f"{path}: {exc}")
                    continue
                if result is not None:
                    done += 1
                    before += result[0]
                    after += result[1]
    finally:
        await pool.close()
    verb = "rewrote" if apply else "would check"
    print(f"{verb} {done} of {len(paths)} objects; {failed} failed")
    if apply:
        print(f"{before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args().apply)))


if __name__ == "__main__":
    main()
