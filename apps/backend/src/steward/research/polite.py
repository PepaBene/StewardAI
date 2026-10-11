"""Fetching pages that a web search or an AI proposed, politely and safely.

Every fetch of a page the owner did not enter goes through here: the host must resolve to
public addresses only (no worker-internal network), the site's robots.txt must allow the
fetch (unless the owner listed the site as exempt), and requests to one host are spaced out.
No logins, no paywall or bot-check circumvention: a site that refuses is skipped and the
reason is recorded.
"""

import asyncio
import hashlib
import re
import time
import urllib.robotparser
from urllib.parse import urljoin, urlsplit

import httpx

from steward.documents.fetch import FetchError, Resolver, check_public_url, resolve_host
from steward.fundamentals.ir import FetchedDocument, IrError, fetch_document
from steward.research.browser import BrowserRenderer
from steward.research.browser import available as browser_available

ROBOTS_AGENT = "StewardAI"
MAX_ROBOTS_REDIRECTS = 3
REFUSED = re.compile(r"HTTP (403|429)\b")
CHALLENGE_MARKERS = ("just a moment", "verify you are human", "captcha", "access denied")


def is_js_shell(doc: FetchedDocument) -> bool:
    """An HTML page with scripts but next to no text and no links: the content is drawn by
    JavaScript, which a plain request does not run."""
    if doc.content_type not in ("text/html", "application/xhtml+xml") or len(doc.body) > 400_000:
        return False
    html = doc.body.decode("utf-8", errors="ignore").lower()
    visible = re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", html, flags=re.DOTALL)
    return "<script" in html and html.count("<a ") < 3 and len(" ".join(visible.split())) < 300


def build_fetcher(
    http: httpx.AsyncClient,
    browser_fallback: bool,
    user_agent: str,
    robots_exempt: tuple[str, ...] = (),
) -> "PoliteFetcher":
    """A fetcher, with the browser fallback when the setting asks for it and Playwright is
    installed (otherwise it quietly behaves as a plain fetcher). `robots_exempt` lists the
    sites the owner chose not to apply robots.txt to."""
    browser = BrowserRenderer(user_agent) if browser_fallback and browser_available() else None
    return PoliteFetcher(http, browser=browser, robots_exempt=robots_exempt)


class PoliteFetcher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        min_interval: float = 2.0,
        resolve: Resolver = resolve_host,
        browser: BrowserRenderer | None = None,
        robots_exempt: tuple[str, ...] = (),
    ) -> None:
        self._http = http
        self._browser = browser
        self._robots_exempt = tuple(d.lower().lstrip(".") for d in robots_exempt)
        self._min_interval = min_interval
        self._resolve = resolve
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Tests route requests through a MockTransport; nothing leaves the process.
        self._offline = isinstance(getattr(http, "_transport", None), httpx.MockTransport)

    async def _robots_for(
        self, scheme: str, host: str
    ) -> urllib.robotparser.RobotFileParser | None:
        """The parsed robots.txt, or None when it could not be read (then nothing is fetched:
        a 4xx means no restrictions, an error or 5xx is treated as closed)."""
        if host in self._robots:
            return self._robots[host]
        parser: urllib.robotparser.RobotFileParser | None
        try:
            robots_url = f"{scheme}://{host}/robots.txt"
            response = await self._http.get(robots_url, timeout=15.0)
            # The client does not follow redirects. Follow a few by hand (robots.txt moved to
            # https or www); one that never settles is unknown, so treated as closed.
            for _ in range(MAX_ROBOTS_REDIRECTS):
                location = response.headers.get("location")
                if not 300 <= response.status_code < 400 or not location:
                    break
                robots_url = urljoin(robots_url, location)
                if urlsplit(robots_url).scheme not in ("http", "https"):
                    break
                response = await self._http.get(robots_url, timeout=15.0)
            if response.status_code >= 500 or 300 <= response.status_code < 400:
                parser = None
            else:
                parser = urllib.robotparser.RobotFileParser()
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser.parse([])  # no robots.txt: no restrictions
        except httpx.HTTPError:
            parser = None
        self._robots[host] = parser
        return parser

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            return False
        if any(host == d or host.endswith(f".{d}") for d in self._robots_exempt):
            return True  # the owner's choice for this site (see `robots_exempt_domains`)
        parser = await self._robots_for(parts.scheme or "https", host)
        return parser is not None and parser.can_fetch(ROBOTS_AGENT, url)

    async def _wait_turn(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            gap = time.monotonic() - self._last.get(host, 0.0)
            if gap < self._min_interval and not self._offline:
                await asyncio.sleep(self._min_interval - gap)
            self._last[host] = time.monotonic()

    async def get(self, url: str, max_bytes: int) -> FetchedDocument:
        """The page, or IrError saying why it was not fetched. With a browser fallback, a
        refusal (HTTP 403/429) or an empty JavaScript shell is retried once in the browser."""
        host = (urlsplit(url).hostname or "").lower()
        if not self._offline:
            try:
                await check_public_url(url, self._resolve)
            except FetchError as exc:
                raise IrError(str(exc)) from exc
        if not await self.allowed(url):
            raise IrError(f"{host} robots.txt does not allow fetching {url}")
        await self._wait_turn(host)
        try:
            doc = await fetch_document(self._http, url, max_bytes=max_bytes)
        except IrError as exc:
            if self._browser is not None and REFUSED.search(str(exc)):
                return await self._render(url, host, max_bytes)
            raise
        if self._browser is not None and is_js_shell(doc):
            return await self._render(url, host, max_bytes)
        return doc

    async def _render(self, url: str, host: str, max_bytes: int) -> FetchedDocument:
        assert self._browser is not None
        await self._wait_turn(host)
        html = await self._browser.render(url)
        if html is None or any(m in html[:3000].lower() for m in CHALLENGE_MARKERS):
            raise IrError(f"blocked: {host} would not show {url} to a plain request or a browser")
        body = html.encode()[:max_bytes]
        return FetchedDocument(
            url=url,
            body=body,
            content_type="text/html",
            last_modified=None,
            sha256=hashlib.sha256(body).hexdigest(),
        )

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
