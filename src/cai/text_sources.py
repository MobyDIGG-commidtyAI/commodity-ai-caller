"""Free text sources.

1. Arctic Shift — Pushshift replacement, free, unauthenticated, Reddit 2005→now.
   https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=..&query=..&after=..&limit=100
   (official Reddit API has no historical search; X full-archive is Enterprise-only — both descoped)
2. RSS feeds — hardware news (Tom's Hardware, TechPowerUp, ...). Only recent items via RSS;
   for 2yr backfill use the site's archive pages or Google News RSS with a date query.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

import feedparser
import requests

AS_BASE = "https://arctic-shift.photon-reddit.com/api"
UA = {"User-Agent": "commodity-ai-hackathon/0.1 (contact: team)"}

DEFAULT_SUBS = {
    "RAM": ["buildapc", "hardware", "pcmasterrace", "buildapcsales"],
    "SSD": ["buildapc", "hardware", "DataHoarder", "buildapcsales"],
    "GPU": ["buildapc", "hardware", "nvidia", "Amd", "buildapcsales"],
}

RSS_FEEDS = {
    "tomshardware": "https://www.tomshardware.com/feeds/all",
    "techpowerup": "https://www.techpowerup.com/rss/news.xml",
    "anandtech_archive": "https://www.anandtech.com/rss/",
}


def _eid(source: str, ext: str) -> str:
    return hashlib.sha1(f"{source}|{ext}".encode()).hexdigest()


def _as_get(params: dict, retries: int = 4) -> list[dict]:
    for i in range(retries):
        try:
            r = requests.get(f"{AS_BASE}/posts/search", params=params, headers=UA, timeout=90)
        except requests.RequestException:
            time.sleep(5 * (i + 1)); continue
        if r.status_code == 200:
            return r.json().get("data", [])
        # 422 "Timeout. Maybe slow down a bit" / 429 -> back off and retry
        time.sleep(8 * (i + 1))
    return []


def arctic_shift_posts(subreddit: str, query: str, after: str, before: str | None = None,
                       limit: int = 100, max_pages: int = 20, sleep: float = 1.5,
                       chunk_days: int = 45) -> list[dict]:
    """Big subs time out on long windows -> walk the range in `chunk_days` slices.
    `after`/`before` are YYYY-MM-DD. Returns raw post dicts (deduped by id)."""
    from datetime import timedelta
    start = datetime.strptime(after, "%Y-%m-%d")
    end = datetime.strptime(before, "%Y-%m-%d") if before else datetime.utcnow()
    out, seen, pages = [], set(), 0
    cur = start
    while cur < end and pages < max_pages:
        nxt = min(cur + timedelta(days=chunk_days), end)
        cursor = cur.strftime("%Y-%m-%d")
        while pages < max_pages:
            data = _as_get({"subreddit": subreddit, "query": query, "after": cursor,
                            "before": nxt.strftime("%Y-%m-%d"), "limit": limit, "sort": "asc"})
            pages += 1
            new = [d for d in data if d["id"] not in seen]
            for d in new:
                seen.add(d["id"]); out.append(d)
            time.sleep(sleep)
            if len(data) < limit:
                break
            cursor = str(int(max(d["created_utc"] for d in data)) + 1)
        cur = nxt
    return out


def reddit_to_text_rows(posts: list[dict], subreddit: str, category: str, sku: str | None = None) -> list[dict]:
    rows = []
    for p in posts:
        body = (p.get("selftext") or "")[:4000]
        if body in ("[removed]", "[deleted]"):
            body = ""
        rows.append({
            "event_id": _eid(f"reddit:{subreddit}", p["id"]),
            "sku": sku, "category": category,
            "ts": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).isoformat(),
            "source": f"reddit:{subreddit}", "external_id": p["id"],
            "url": f"https://reddit.com{p.get('permalink','')}",
            "title": p.get("title", ""), "body": body, "lang": "en",
            "cluster_id": None, "is_canonical": 1, "score_fast": None, "score_judge": None,
            "meta": json.dumps({"score": p.get("score"), "num_comments": p.get("num_comments"),
                                "flair": p.get("link_flair_text")}),
        })
    return rows


def rss_to_text_rows(feed_key: str, url: str, keyword_filter: list[str] | None = None,
                     category: str = "MARKET") -> list[dict]:
    fp = feedparser.parse(url)
    rows = []
    for e in fp.entries:
        text = f"{e.get('title','')} {e.get('summary','')}"
        if keyword_filter and not any(k.lower() in text.lower() for k in keyword_filter):
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        ts_iso = datetime(*ts[:6], tzinfo=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
        rows.append({
            "event_id": _eid(f"rss:{feed_key}", e.get("id") or e.get("link")),
            "sku": None, "category": category, "ts": ts_iso,
            "source": f"rss:{feed_key}", "external_id": e.get("id") or e.get("link"),
            "url": e.get("link"), "title": e.get("title", ""), "body": e.get("summary", "")[:4000],
            "lang": "en", "cluster_id": None, "is_canonical": 1, "score_fast": None, "score_judge": None,
            "meta": json.dumps({"tags": [t.get("term") for t in e.get("tags", [])]}),
        })
    return rows
