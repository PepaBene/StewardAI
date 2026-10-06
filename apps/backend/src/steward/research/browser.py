"""A headless-browser fallback for pages a plain request cannot read.

Used only after the plain fetch got HTTP 403/429 or a page that is an empty JavaScript shell,
and only for a URL robots.txt allows. It reads what a visitor's browser would show: it never
logs in, never gets past a paywall, never solves a challenge and does not disguise itself (the
browser keeps its default fingerprint apart from our identifying User-Agent). A site that
still refuses is left to the owner. One page at a time; Chromium is started on the first use
and closed with the fetcher.

Playwright is an optional dependency (the `browser` extra, installed in the Docker image with
Chromium). Without it `available()` is false and the fetcher behaves as before.
"""

import importlib
import importlib.util
import logging
from typing import Any
from urllib.parse import urlsplit

from steward.documents.fetch import FetchError, Resolver, check_public_url, resolve_host

logger = logging.getLogger("steward.browser")

NAVIGATION_TIMEOUT_MS = 30_000
BLOCKED_RESOURCES = {"image", "media", "font"}


def available() -> bool:
    return importlib.util.find_spec("playwright") is not None


class BrowserRenderer:
    def __init__(self, user_agent: str, resolve: Resolver = resolve_host) -> None:
        self._user_agent = user_agent
        self._resolve = resolve
        self._playwright: Any = None
        self._browser: Any = None
        self._host_ok: dict[str, bool] = {}

    async def _start(self) -> None:
        if self._browser is not None:
            return
        api = importlib.import_module("playwright.async_api")
        self._playwright = await api.async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def _allowed_host(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host:
            return False
        if host not in self._host_ok:
            try:
                await check_public_url(url, self._resolve)
                self._host_ok[host] = True
            except FetchError:
                self._host_ok[host] = False
        return self._host_ok[host]

    async def render(self, url: str) -> str | None:
        """The rendered HTML, or None when the browser is unavailable or the page failed."""
        if not available():
            return None
        try:
            await self._start()
            context = await self._browser.new_context(user_agent=self._user_agent)
        except Exception:
            logger.warning("browser fallback could not start", exc_info=True)
            return None
        try:

            async def guard(route: Any) -> None:
                request = route.request
                if request.resource_type in BLOCKED_RESOURCES or not await self._allowed_host(
                    request.url
                ):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)
            if not await self._allowed_host(page.url):
                return None
            html = await page.content()
            return str(html)
        except Exception:
            logger.info("browser fallback failed for %s", url, exc_info=True)
            return None
        finally:
            await context.close()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = self._playwright = None
