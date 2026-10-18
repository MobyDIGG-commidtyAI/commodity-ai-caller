"""Tag every text_event row with a news genre for พี่เบิร์ด's per-genre impact model.

Three genres (asked for by พี่ชิน, 2026-10-16): social_network, industry_news, general_news.
The genre comes from the publisher, not the text, so it is reproducible and auditable:
  1. source is a community (Reddit posts/comments, Hacker News)       -> social_network
  2. source is industry by definition (TrendForce)                     -> industry_news
  3. press-release wire (GlobeNewswire ...) whose title names the chip
     industry -> industry_news; any other wire item -> general_news ("wire-offtopic")
  4. publisher / Google News suffix / URL domain matches an outlet in
     config/genres.yaml industry_outlets                               -> industry_news
  5. a known publisher or domain that is not on the list                -> general_news
  6. no publisher and no URL (e.g. the financial-news-2024 shard)      -> general_news, rule "default"
genre_rule records which step decided, so any row can be traced.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GENRES = ("social_network", "industry_news", "general_news")
# dataset names that partner rows carry in place of a real publisher
NOT_A_PUBLISHER = {"", "financial-news-2024", "finance", "feeds2", "search"}


def load_config(path: Path = ROOT / "config" / "genres.yaml") -> dict:
    cfg = yaml.safe_load(Path(path).read_text())
    cfg["industry_re"] = re.compile("|".join(f"(?:{p})" for p in cfg["industry_outlets"]), re.I)
    cfg["wire_re"] = re.compile("|".join(f"(?:{p})" for p in cfg["press_wires"]), re.I)
    cfg["terms_re"] = re.compile(cfg["industry_terms"], re.I)
    return cfg


def domain(url: str | None) -> str:
    m = re.match(r"https?://([^/?#]+)", url or "")
    return m.group(1).lower().removeprefix("www.") if m else ""


def publisher_of(source: str, title: str | None, meta: dict) -> str:
    pub = (meta.get("publisher") or "").strip()
    if pub in NOT_A_PUBLISHER:
        pub = ""
    if not pub and source == "chin:google-news-rss-recent" and title and " - " in title:
        pub = title.rsplit(" - ", 1)[-1].strip()      # Google News titles end with " - Publisher"
    return pub


def classify(source: str, url: str | None = None, title: str | None = None, meta: dict | str | None = None,
             cfg: dict | None = None) -> tuple[str, str]:
    """-> (genre, genre_rule)."""
    cfg = cfg or load_config()
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    meta = meta or {}
    if any(source == s or source.startswith(s) for s in cfg["social_sources"]):
        return "social_network", "source:" + source.split(":")[0]
    if source in cfg["industry_sources"]:
        return "industry_news", "source:" + source
    pub, dom = publisher_of(source, title, meta), domain(url)
    wire = next((v for v in (pub, dom) if v and cfg["wire_re"].search(v)), None)
    if wire:
        if cfg["terms_re"].search(title or ""):
            return "industry_news", f"wire:{wire}"
        return "general_news", f"wire-offtopic:{wire}"
    for kind, value in (("publisher", pub), ("domain", dom)):
        if value and cfg["industry_re"].search(value):
            return "industry_news", f"{kind}:{value}"
    if pub or dom:
        return "general_news", f"{'publisher' if pub else 'domain'}:{pub or dom}"
    return "general_news", "default"


def tag_rows(rows: list[dict], cfg: dict | None = None) -> list[dict]:
    """rows: dicts with event_id, source, url, title, meta -> [{event_id, genre, genre_rule}]."""
    cfg = cfg or load_config()
    out = []
    for r in rows:
        g, rule = classify(r["source"], r.get("url"), r.get("title"), r.get("meta"), cfg)
        out.append({"event_id": r["event_id"], "genre": g, "genre_rule": rule})
    return out
