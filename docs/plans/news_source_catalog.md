# News source catalog

Probed 2026-10-10 with `curl -sL --max-time 20 -A "Mozilla/5.0 StewardAI-research"`. Status is the final HTTP status after redirects; "ok" means 200 plus an XML/JSON content type (item parsing was not re-checked here). robots.txt was read for the feed's own host, `User-agent: *` group only; AI-bot-specific groups are ignored. "Terms" cells cite a URL only when a sentence was actually read; otherwise "not found" (page missing or no sentence located) or "unverified". Reuse terms are not assessed: owner must review terms for every row.

## Outlet feeds (tier 2, seeded in `_default_sources`)

Terms for all outlets: not found (outlet ToS pages not fetched), owner must review terms. Key/cost: none required for the feed. Recommended poll: 15 min unless noted; where `Crawl-delay` exists, keep at least that gap between requests and poll no faster than 30 min.

| id | Feed URL | Status | robots.txt (`*` group) |
|---|---|---|---|
| bloomberg-markets / -politics / -technology | feeds.bloomberg.com/{markets,politics,technology}/news.rss | 200, redirects to www.bloomberg.com/feeds/... | feeds.bloomberg.com: 404 (none); www.bloomberg.com robots unverified |
| ft-home / ft-markets / ft-companies | www.ft.com/rss/home, /markets?format=rss, /companies?format=rss | 200 (home redirects to /rss/home/international) | no feed path disallowed; `Crawl-Delay: 1` |
| bbc-business / -world / -asia | feeds.bbci.co.uk/news/{business,world,world/asia}/rss.xml | 200 | `Disallow: /mpsearch` and two old article paths only |
| guardian-business | www.theguardian.com/uk/business/rss | 200 | no feed path disallowed in the lines inspected (first 12 Disallow lines) |
| sky-business | feeds.skynews.com/feeds/rss/business.xml | 200 | no feed-path Disallow seen |
| telegraph-business | www.telegraph.co.uk/business/rss.xml | **402** (text/html) | `User-agent: * Allow: /`; the feed is blocked at the server, not by robots |
| nikkei-asia | asia.nikkei.com/rss/feed/nar | 200 | `Disallow: /search`, `/api`, `/cms`, `/__service`; /rss not listed |
| japan-times | www.japantimes.co.jp/feed/ | 200 | robots.txt itself returned 403: unverified |
| scmp-business / scmp-china | www.scmp.com/rss/92/feed, /rss/4/feed (redirect to trailing slash) | 200 | `Crawl-delay: 10`; /rss not listed |
| caixin-global | www.caixinglobal.com/rss/ | **403** | no `*` group; only GPTBot/CCBot `Disallow: /` |
| xinhua-business | www.xinhuanet.com/english/rss/businessrss.xml | 200 | `Allow: /` |
| straits-times | www.straitstimes.com/news/business/rss.xml | 200 | `Crawl-delay: 10`; feed path not disallowed |
| yonhap | en.yna.co.kr/RSS/news.xml | 200 | `Allow:/`; disallows /search/, /program/, /web/ |
| economic-times | economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms | 200 | `Disallow: /*rssarticleshow*` (article URLs of that form); feed path not disallowed |
| livemint | www.livemint.com/rss/markets | 200 | no feed-path Disallow seen |
| dw-business | rss.dw.com/rdf/rss-en-bus | 200 | no `*` group; named bots only (Slurp, MJ12bot...) |
| handelsblatt | www.handelsblatt.com/contentexport/feed/finanzen | 200, redirects to feeds.cms.handelsblatt.com/finanzen | www host: `Disallow: /api/*`; feed host robots unverified |
| faz-finanzen | www.faz.net/rss/aktuell/finanzen/ | 200 | `Disallow: /json/`, `/suche/`; /rss not listed |
| tagesschau-wirtschaft | www.tagesschau.de/wirtschaft/index~rss2.xml | 200 | `Disallow: /ardimport/`, `/suche2.html?*` |
| globe-and-mail-business | www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/ | 200 | `Disallow: /feeds/` (a different prefix than /arc/outboundfeeds/) |
| financial-post | financialpost.com/feed | 200 | `Disallow: /*?` (any URL with a query string); /feed has none |
| cbc-business | www.cbc.ca/webfeed/rss/rss-business | 200 | no rss/webfeed Disallow in the `*` group |
| aljazeera | www.aljazeera.com/xml/rss/all.xml | 200 | `Disallow: /api`, `/search/`; /xml not listed |
| cnbc-markets | search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114 | 200 | **`User-agent: * Disallow: /`** (only Amazonbot is allowed one feed) |
| marketwatch-top / wsj-markets | feeds.content.dowjones.io/public/rss/{mw_topstories,RSSMarketsMain} | 200 | robots.txt returned 403: unverified |
| hn-cz | hn.cz/?m=rss | 200 | `allow: /`, `crawl-delay: 1` |
| e15 | www.e15.cz/rss | 200 | disallows only query patterns (`/*?*page=`, `/*?*nocache*`); /rss not listed |
| seznam-zpravy | www.seznamzpravy.cz/rss | 200 | disallows search/gallery query patterns; /rss not listed |

## Newswire and discovery

| id | Endpoint | Status | robots.txt | Terms / limits | Key / cost | Poll |
|---|---|---|---|---|---|---|
| reuters-sitemap | www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml | 200 application/xml | **`User-agent: * Allow: /plus/ Disallow: /`**; the same file lists this URL as a `SITEMAP:` line | not found (Reuters ToS not fetched); owner must review terms | none | 15 min only if the owner accepts the robots conflict; otherwise keep off |
| gdelt-doc | api.gdeltproject.org/api/v2/doc/doc?query=...&format=json | **429** on a cold request (no body) | robots.txt: 404 (none) | "all datasets released by the GDELT Project are available for unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee" (https://www.gdeltproject.org/about.html). Numeric API rate limit: not found | none | keep disabled; back off on 429, at most 1 request per several minutes until the N0 probe |

## Central banks (tier 1)

| id | Feed URL | Status | robots.txt | Terms | Key / cost | Poll |
|---|---|---|---|---|---|---|
| fed-press | www.federalreserve.gov/feeds/press_monetary.xml | 200 text/xml | 404 (none) | "Unless otherwise indicated, information on Board's website is in the public domain and may be copied and distributed without permission. Please cite to the Board as the source" (https://www.federalreserve.gov/disclaimer.htm). Rate limit: not found | none | 30 min |
| ecb-press | www.ecb.europa.eu/rss/press.html | 200 application/rss+xml | disallows `/*_content.<lang>.html$` patterns; the feed is not matched | "users of this website may make free use of the information obtained directly from it ... the ECB must be cited as the source" (https://www.ecb.europa.eu/services/disclaimer/html/index.en.html). Rate limit: not found | none | 30 min |
| cnb-press | www.cnb.cz/en/.content/rss-feed/rss-feed_tz.xml | 200 application/xml | `Disallow: /export/sites/nc/`, `/export/sites/pnu/`; feed not matched | not found (guessed legal page 404); owner must review terms | none | 30 min |
| bundesbank (not seeded) | www.bundesbank.de/service/rss/en/633306/feed.rss ("Latest", RSS 2.0, 200). Listing page: www.bundesbank.de/en/homepage/rss (200). Candidate /en/service/rss: **404** | 200 | `User-agent: * Crawl-delay: 10`; no `Disallow` for `*` (the `Disallow: /` entries belong to named bots) | not found (copyright / terms guesses 404) ; owner must review terms | none | 60 min, 10 s between requests |

## Official filings (not seeded unless stated)

| Source | Machine-readable endpoint (verified) | robots.txt | Terms / limits | Key / cost | Poll |
|---|---|---|---|---|---|
| HKEXnews | `https://www1.hkexnews.hk/ncms/json/eds/lcisehk1relsde_1.json` (English, 200 JSON, `newsInfoLst` with title, webPath, stock code, relTime). `...lcisehk1relsdc_1.json` is the Chinese variant (200); `..._c.json` and `...relsd_1.json` are 404. Page 1 only (`maxNumOfFile: 1`). Unofficial, undocumented path | www and www1: 404 (none) | Strong restriction: "You are not permitted to conduct, facilitate, enable, authorise or permit any text or data mining or web scraping in relation to this Website" (https://www.hkex.com.hk/Global/Exchange/Disclaimer?sc_lang=en, section 5). Owner must review terms | none | do not poll until the owner decides; if allowed, 15 min |
| CNINFO | POST `https://www.cninfo.com.cn/new/hisAnnouncement/query` (form: pageNum, pageSize, column=szse, tabName=fulltext, seDate=YYYY-MM-DD~YYYY-MM-DD) returned JSON with `totalAnnouncement: 1755` for 2026-10-09; a GET returned 500. Unofficial, undocumented | /robots.txt: 404 (error page) | not found (/new/aboutUs 404); owner must review terms | none seen | 15 min during CN market hours if terms allow; unverified otherwise |
| JPX TDnet | none official found. List pages `https://www.release.tdnet.info/inbs/I_list_001_YYYYMMDD.html` return 200 HTML (`<meta name="robots" content="noindex,nofollow">`). Third party `webapi.yanoshin.jp/webapi/tdnet/list/today.json` returned valid JSON but 0 items on 2026-10-10 (a Saturday); weekday content unverified | www.release.tdnet.info: **`User-agent: * Disallow: /`**; www.jpx.co.jp: `Disallow:` (empty) | not found (jpx terms URL guess 404); paid JPX data service: unverified | none for the HTML | do not poll TDnet pages (robots); revisit with owner |
| EDINET API v2 | `https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-10-09&type=1` returned HTTP 200 whose body was `{"StatusCode": 401, ... invalid subscription key}` | api host robots returned an HTML page (no file); disclosure2dl root: 403 | EDINET terms (https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html), translated: content may be used under the Public Data License 1.0. API rate limit: not found | key required; cost and registration terms: unverified | daily after the evening filing batch; 1 request per day for the document list |
| OpenDART | `https://opendart.fss.or.kr/api/list.json` without a key redirected to `error.html` (200 HTML) | /robots.txt returned HTML, no rules | site has an "인증키 신청" (key application) and "이용약관" (terms) menu; daily request quota: not found on the fetched page | key required; cost: unverified | 30 min with a key |
| Companies House API | `https://api.company-information.service.gov.uk/company/00000006` returned **401** JSON (no key) | 401 (none readable) | "You can make up to 600 requests within a five-minute period" and exceeding it gives 429 (https://developer-specs.company-information.service.gov.uk/guides/rateLimiting). ToS page: not found (404 on guess) | key required; cost: unverified | at most 1 request per second sustained; daily is enough for filings |
| SEDAR+ | home page 200 (`www.sedarplus.ca/home/`); no public API or feed found | `Disallow: /` listed for named bots only (MJ12bot, SemrushBot, etc.), no `*` group | not found | unverified | not applicable until an endpoint is identified |
| Bundesanzeiger | home page 200; no API or feed found. NLP page `to_nlp_start` exists but its content was not read | `User-agent: * Disallow: /nlp`, `Disallow: /construction_page.html` | not found | unverified | not applicable until an endpoint is identified |

## Findings

- Died or blocked: Telegraph feed returns 402 and Caixin feed 403 (both already `needs_alt`); the GDELT API returned 429 on a cold request (source already disabled).
- robots.txt conflicts for generic agents: `search.cnbc.com` (`Disallow: /`), `www.reuters.com` (`Allow: /plus/`, `Disallow: /`, yet its news sitemap is listed as a Sitemap), `www.release.tdnet.info` (`Disallow: /`). The feeds still return 200. Owner must review terms before enabling these.
- HKEX Disclaimer section 5 prohibits text or data mining and web scraping of the site; the JSON endpoint is undocumented. Owner must review terms.
- Redirects to store as canonical URLs: Bloomberg to `www.bloomberg.com/feeds/...`, FT home to `/rss/home/international`, Handelsblatt to `feeds.cms.handelsblatt.com/finanzen`, SCMP adds a trailing slash.
- Bundesbank candidate `/en/service/rss` is 404; the working feed is `/service/rss/en/633306/feed.rss` (robots `Crawl-delay: 10`).
- Needs a key before use: EDINET v2 (HTTP 200 with a 401 body), OpenDART, Companies House (401). Cost for all three: unverified.
- No machine-readable endpoint found: JPX TDnet (no official one), SEDAR+, Bundesanzeiger.
- The Korea Herald URL from the plan's section 2 is not in `_default_sources`, so it was not re-probed.
- Not verified here: item parsability of each feed, outlet ToS pages, and every "unverified" cell above.
