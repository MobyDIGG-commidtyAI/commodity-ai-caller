"""Reddit via Arctic Shift *without* server-side text search.

Why: since 2026-10 Arctic Shift answers `query=` / `title=` / comments `body=` with an instant
422 "Timeout. Maybe slow down a bit" even on 1-day windows — text search is refused server-side,
smaller windows don't help. The plain listing (subreddit + after + before) still works, so we walk
every post in a sub in time order, filter locally, and upsert page by page.

Resumable: per-sub cursor (last created_utc) in data/raw/reddit_cursor.json.
Comments: fetched per matched post with `comments/search?link_id=<id>` (works, no text search needed).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

AS_BASE = "https://arctic-shift.photon-reddit.com/api"
UA = {"User-Agent": "commodity-ai-hackathon/0.1 (contact: team)"}
ROOT = Path(__file__).resolve().parents[2]
CURSOR_FILE = ROOT / "data" / "raw" / "reddit_cursor.json"
POST_FIELDS = "id,title,selftext,created_utc,score,num_comments,link_flair_text"

# Which subs and from when. `news` subs keep any category hit; others also need a price word.
# `pages_per_day` (optional) = day-sampling for very large subs; omitted = every post.
SUBS = {
    "buildapcsales": {"since": "2022-01-01", "news": True},
    "hardware":      {"since": "2022-01-01", "news": True},
    "DataHoarder":   {"since": "2024-01-01", "news": False},
    # ~850 and ~1000+ posts/day at ~2 s/request: a full walk takes days -> sample the first N pages of each UTC day
    "buildapc":      {"since": "2024-01-01", "news": False, "pages_per_day": 2},
    "pcmasterrace":  {"since": "2025-01-01", "news": False, "pages_per_day": 1},
}

CAT_RE = {
    "RAM": re.compile(r"\b(ddr[345]\w*|lpddr\w*|dram|ram|memory kit|memory prices?|hbm\w*|sodimm|dimm)\b", re.I),
    "SSD": re.compile(r"\b(ssd|ssds|nvme|nand|m\.2|flash prices?)\b", re.I),
    "GPU": re.compile(r"\b(gpu|gpus|graphics cards?|rtx ?\d{4}|radeon|rx ?\d{4})\b", re.I),
}
PRICE_RE = re.compile(r"(\$\s?\d|\bprice|\bpricing|\bcost|expensive|cheap|shortage|\bdeal\b|msrp|tariff|"
                      r"afford|overpriced|spike|surge|skyrocket|doubled|tripled|went up|go down|drop|wait to buy)", re.I)
# buildapcsales flair / [TAG] -> category
BAPCS_TAG = {"RAM": re.compile(r"^(ram|memory)", re.I),
             "SSD": re.compile(r"^(ssd|nvme|m\.2)", re.I),
             "GPU": re.compile(r"^gpu", re.I)}


def _eid(source: str, ext: str) -> str:
    return hashlib.sha1(f"{source}|{ext}".encode()).hexdigest()


def _get(path: str, params: dict, retries: int = 6) -> list[dict] | None:
    for i in range(retries):
        try:
            r = requests.get(f"{AS_BASE}/{path}", params=params, headers=UA, timeout=90)
            if r.status_code == 200:
                return r.json().get("data") or []
            if r.status_code == 400:   # bad request (e.g. invalid field) - retrying won't help
                raise ValueError(f"Arctic Shift 400: {r.text[:200]}")
        except ValueError:
            raise
        except requests.RequestException:
            pass
        time.sleep(min(120, 5 * 2 ** i))
    return None


def load_cursor() -> dict:
    return json.loads(CURSOR_FILE.read_text()) if CURSOR_FILE.exists() else {}


def save_cursor(cur: dict) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cur, indent=1))


def classify(sub: str, title: str, body: str, flair: str | None) -> str | None:
    if sub == "buildapcsales":
        tag = (flair or "") or (re.match(r"\s*\[([^\]]+)\]", title) or [None, ""])[1]
        for cat, rx in BAPCS_TAG.items():
            if rx.search(tag or ""):
                return cat
        return None
    blob = f"{title} {body}"
    hits = {c: len(rx.findall(blob)) for c, rx in CAT_RE.items()}
    cat = max(hits, key=hits.get)
    if hits[cat] == 0:
        return None
    if not SUBS[sub]["news"]:
        # discussion subs: every build post mentions RAM/SSD -> need the price angle in the title
        cat_t, price_t = CAT_RE[cat].search(title), PRICE_RE.search(title)
        if not ((cat_t and PRICE_RE.search(blob)) or (price_t and hits[cat])):
            return None
    return cat


def post_row(p: dict, sub: str, cat: str) -> dict:
    body = (p.get("selftext") or "")[:4000]
    if body in ("[removed]", "[deleted]"):
        body = ""
    return {"event_id": _eid(f"reddit:{sub}", p["id"]), "sku": None, "category": cat,
            "ts": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).isoformat(),
            "source": f"reddit:{sub}", "external_id": p["id"],
            "url": f"https://reddit.com/comments/{p['id']}", "title": p.get("title", ""), "body": body,
            "lang": "en", "cluster_id": None, "is_canonical": 1, "score_fast": None, "score_judge": None,
            "meta": json.dumps({"score": p.get("score"), "num_comments": p.get("num_comments"),
                                "flair": p.get("link_flair_text")})}


def walk_sub(con, sub: str, upsert, until: datetime | None = None, sleep: float = 0.7, log=print) -> int:
    """Walk r/<sub> from cursor (or SUBS[sub]['since']) to `until`, upserting matched posts per page."""
    cursors = load_cursor()
    start = cursors.get(sub) or int(datetime.strptime(SUBS[sub]["since"], "%Y-%m-%d")
                                    .replace(tzinfo=timezone.utc).timestamp())
    end = int((until or datetime.now(timezone.utc)).timestamp())
    kept = seen = 0
    after = start
    ppd = SUBS[sub].get("pages_per_day")
    day_pages = 0
    while after < end:
        # bounded window: `before=now` makes Arctic Shift scan years per request -> 422 "Timeout" + backoff
        day_end = (after // 86400 + 1) * 86400
        win_end = min(day_end if ppd else after + 7 * 86400, end)
        data = _get("posts/search", {"subreddit": sub, "after": after, "before": win_end, "limit": 100,
                                     "sort": "asc", "fields": POST_FIELDS})
        if data is None:
            log(f"r/{sub}: giving up at {datetime.fromtimestamp(after, tz=timezone.utc):%F} (API errors); cursor saved")
            break
        rows = []
        for p in data:
            cat = classify(sub, p.get("title", ""), p.get("selftext") or "", p.get("link_flair_text"))
            if cat:
                rows.append(post_row(p, sub, cat))
        if rows:
            upsert(con, "text_event", rows)
        kept += len(rows); seen += len(data)
        day_pages += 1
        if len(data) < 100 or (ppd and day_pages >= ppd):
            after, day_pages = win_end, 0                  # window exhausted (or day sample taken)
        else:
            after = int(max(p["created_utc"] for p in data)) + 1
        cursors = load_cursor(); cursors[sub] = after; save_cursor(cursors)
        if data and (seen // 100) % 20 == 0 and len(data) == 100 or after % (86400 * 30) < 86400 and day_pages == 0:
            log(f"r/{sub}: {datetime.fromtimestamp(after, tz=timezone.utc):%F} seen={seen} kept={kept}")
        time.sleep(sleep)
    log(f"r/{sub}: done seen={seen} kept={kept}")
    return kept


def comments_for_post(post_id: str, max_pages: int = 1) -> list[dict]:
    out, after = [], None
    for _ in range(max_pages):
        params = {"link_id": post_id, "limit": 100, "sort": "asc", "fields": "id,body,created_utc,score"}
        if after:
            params["after"] = after
        data = _get("comments/search", params, retries=4)
        if not data:
            break
        out += data
        if len(data) < 100:
            break
        after = int(max(c["created_utc"] for c in data)) + 1
        time.sleep(0.7)
    return out


def comment_rows(comments: list[dict], sub: str, cat: str, post_id: str) -> list[dict]:
    rows = []
    for c in comments:
        body = (c.get("body") or "")[:4000]
        if body in ("[removed]", "[deleted]", ""):
            continue
        rows.append({"event_id": _eid(f"reddit_c:{sub}", c["id"]), "sku": None, "category": cat,
                     "ts": datetime.fromtimestamp(c["created_utc"], tz=timezone.utc).isoformat(),
                     "source": f"reddit_c:{sub}", "external_id": c["id"],
                     "url": f"https://reddit.com/comments/{post_id}/_/{c['id']}", "title": "", "body": body,
                     "lang": "en", "cluster_id": None, "is_canonical": 1, "score_fast": None, "score_judge": None,
                     "meta": json.dumps({"score": c.get("score"), "link_id": post_id})})
    return rows
