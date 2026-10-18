"""Free, no-key, no-wait sources that work from anywhere (all verified from sandbox).

price_daily  : yfinance (commodity futures + memory-maker stocks as proxies)
attention    : Wikipedia pageviews (daily) -> stored in price_daily with source='wiki_pageviews' (unit=views)
text_event   : Hacker News (Algolia), Reddit comments (Arctic Shift `body=` search)
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "commodity-ai-hackathon/0.1"}

# ---- yfinance universe: real commodities (slide: copper/coffee/boxes) + chip-maker proxies ----
YF_SERIES = {
    # commodities (futures, USD)
    "CMD-COPPER":      ("HG=F", "USD", "Copper futures"),
    "CMD-ALUMINUM":    ("ALI=F", "USD", "Aluminum futures"),
    "CMD-COFFEE":      ("KC=F", "USD", "Coffee futures"),
    "CMD-CRUDE-WTI":   ("CL=F", "USD", "WTI crude"),
    "CMD-NATGAS":      ("NG=F", "USD", "Natural gas"),
    "CMD-WHEAT":       ("ZW=F", "USD", "Wheat"),
    "CMD-SUGAR":       ("SB=F", "USD", "Sugar"),
    "CMD-GOLD":        ("GC=F", "USD", "Gold"),
    # memory / storage makers = market's price expectation for RAM/SSD
    "EQ-MICRON":       ("MU", "USD", "Micron"),
    "EQ-SKHYNIX":      ("000660.KS", "KRW", "SK hynix"),
    "EQ-SAMSUNG":      ("005930.KS", "KRW", "Samsung Electronics"),
    "EQ-WDC":          ("WDC", "USD", "Western Digital"),
    "EQ-SNDK":         ("SNDK", "USD", "Sandisk"),
    "EQ-KIOXIA":       ("285A.T", "JPY", "Kioxia"),
    "EQ-NVIDIA":       ("NVDA", "USD", "NVIDIA"),
    "EQ-AMD":          ("AMD", "USD", "AMD"),
    "EQ-SOXX":         ("SOXX", "USD", "Semiconductor ETF"),
    # macro
    "FX-USDTHB":       ("THB=X", "THB", "USD/THB"),
    "FX-USDKRW":       ("KRW=X", "KRW", "USD/KRW"),
    "MACRO-US10Y":     ("^TNX", "PCT", "US 10y yield"),
}

WIKI_PAGES = {  # attention proxy (Google Trends substitute). sku_or_series -> en.wikipedia article
    "ATT-WIKI-DDR5":  "DDR5_SDRAM",
    "ATT-WIKI-DDR4":  "DDR4_SDRAM",
    "ATT-WIKI-DRAM":  "Dynamic_random-access_memory",
    "ATT-WIKI-SSD":   "Solid-state_drive",
    "ATT-WIKI-NVME":  "NVM_Express",
    "ATT-WIKI-NAND":  "Flash_memory",
    "ATT-WIKI-GPU":   "Graphics_processing_unit",
    "ATT-WIKI-RTX50": "GeForce_RTX_50_series",
    "ATT-WIKI-MICRON": "Micron_Technology",
    "ATT-WIKI-SKHYNIX": "SK_Hynix",
    "ATT-WIKI-COPPER": "Copper",
    "ATT-WIKI-COFFEE": "Coffee",
}


def _eid(source: str, ext: str) -> str:
    return hashlib.sha1(f"{source}|{ext}".encode()).hexdigest()


def yfinance_rows(period: str = "5y") -> list[dict]:
    import yfinance as yf
    tickers = [v[0] for v in YF_SERIES.values()]
    df = yf.download(tickers, period=period, progress=False, auto_adjust=True)["Close"]
    rows = []
    for sku, (tk, cur, name) in YF_SERIES.items():
        if tk not in df.columns:
            continue
        s = df[tk].dropna()
        for d, v in s.items():
            rows.append({"sku": sku, "source": "yfinance", "date": d.strftime("%Y-%m-%d"), "price": float(v),
                         "currency": cur, "meta": json.dumps({"ticker": tk, "name": name})})
    return rows


def wiki_pageviews_rows(start: str = "20240101", end: str | None = None) -> list[dict]:
    end = end or datetime.utcnow().strftime("%Y%m%d")
    rows = []
    for sku, page in WIKI_PAGES.items():
        url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{page}/daily/{start}/{end}"
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200:
            continue
        for it in r.json().get("items", []):
            d = it["timestamp"][:8]
            rows.append({"sku": sku, "source": "wiki_pageviews", "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                         "price": float(it["views"]), "currency": "VIEWS", "meta": json.dumps({"page": page})})
        time.sleep(0.3)
    return rows


_GENERIC = {"price", "prices"}


def hn_relevant(query: str, text: str) -> bool:
    """Algolia's HN search is typo-tolerant and matches partial words ("NAND prices" -> "and prices"),
    so keep a hit only if every non-generic query word appears as a whole word in its title or text."""
    words = [w for w in query.lower().split() if w not in _GENERIC]
    return all(re.search(r"\b" + re.escape(w) + r"\b", text, re.I) for w in words)


def hn_rows(queries: list[str], since: str = "2024-01-01", category: str = "MARKET") -> list[dict]:
    since_i = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    rows, seen = [], set()
    for q in queries:
        for page in range(0, 10):
            r = requests.get("https://hn.algolia.com/api/v1/search_by_date",
                             params={"query": q, "tags": "(story,comment)", "numericFilters": f"created_at_i>{since_i}",
                                     "typoTolerance": "false", "hitsPerPage": 100, "page": page},
                             headers=UA, timeout=60)
            if r.status_code != 200:
                break
            hits = r.json().get("hits", [])
            for h in hits:
                if h["objectID"] in seen:
                    continue
                seen.add(h["objectID"])
                title = h.get("title") or h.get("story_title") or ""
                body = (h.get("comment_text") or h.get("story_text") or "")[:4000]
                if not hn_relevant(q, f"{title} {body}"):
                    continue
                rows.append({"event_id": _eid("hn", h["objectID"]), "sku": None, "category": category,
                             "ts": datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc).isoformat(),
                             "source": "hn", "external_id": h["objectID"],
                             "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                             "title": title, "body": body, "lang": "en", "cluster_id": None, "is_canonical": 1,
                             "score_fast": None, "score_judge": None,
                             "meta": json.dumps({"points": h.get("points"), "query": q})})
            if len(hits) < 100:
                break
            time.sleep(0.5)
    return rows


def reddit_comments(subreddit: str, body_query: str, after: str, before: str | None = None,
                    limit: int = 100, max_pages: int = 10, chunk_days: int = 30) -> list[dict]:
    """Arctic Shift comments endpoint uses `body=` (not `query=`). Long windows time out (422) -> chunk."""
    from datetime import timedelta
    start = datetime.strptime(after, "%Y-%m-%d")
    end = datetime.strptime(before, "%Y-%m-%d") if before else datetime.utcnow()
    out, seen, pages, cur = [], set(), 0, start
    while cur < end and pages < max_pages:
        nxt = min(cur + timedelta(days=chunk_days), end)
        cursor = cur.strftime("%Y-%m-%d")
        while pages < max_pages:
            data = []
            for i in range(3):
                try:
                    r = requests.get("https://arctic-shift.photon-reddit.com/api/comments/search",
                                     params={"subreddit": subreddit, "body": body_query, "after": cursor,
                                             "before": nxt.strftime("%Y-%m-%d"), "limit": limit, "sort": "asc"},
                                     headers=UA, timeout=90)
                    if r.status_code == 200:
                        data = r.json().get("data", []); break
                except requests.RequestException:
                    pass
                time.sleep(8 * (i + 1))
            pages += 1
            new = [d for d in data if d["id"] not in seen]
            for d in new:
                seen.add(d["id"]); out.append(d)
            time.sleep(1.5)
            if len(data) < limit:
                break
            cursor = str(int(max(d["created_utc"] for d in data)) + 1)
        cur = nxt
    return out


def reddit_comment_rows(comments: list[dict], subreddit: str, category: str) -> list[dict]:
    rows = []
    for c in comments:
        body = (c.get("body") or "")[:4000]
        if body in ("[removed]", "[deleted]", ""):
            continue
        rows.append({"event_id": _eid(f"reddit_c:{subreddit}", c["id"]), "sku": None, "category": category,
                     "ts": datetime.fromtimestamp(c["created_utc"], tz=timezone.utc).isoformat(),
                     "source": f"reddit_c:{subreddit}", "external_id": c["id"],
                     "url": f"https://reddit.com{c.get('permalink','')}", "title": "", "body": body, "lang": "en",
                     "cluster_id": None, "is_canonical": 1, "score_fast": None, "score_judge": None,
                     "meta": json.dumps({"score": c.get("score"), "link_id": c.get("link_id")})})
    return rows
