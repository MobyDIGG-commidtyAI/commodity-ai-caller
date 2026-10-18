"""TrendForce "Memory Spot Price Update" crawler.

Why: this is the closest thing to a real commodity price for RAM (DDR4 1Gx8 chip)
and NAND (512Gb TLC wafer). Weekly, free, goes back years, and every article
carries a narrative paragraph = ground-truth-ish sentiment text.

Outputs
  price_daily rows  : sku=TF-DDR4-1GX8 / TF-NAND-512GB-TLC, source=trendforce_spot
  text_event rows   : one per article, category=MARKET
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
SEARCH = "https://www.trendforce.com/news/page/{page}/?s={query}"
# The weekly report is not always titled "Memory Spot Price Update" (2023-24 titles vary, e.g.
# "DRAM and NAND Flash Spot Prices Drop as Demand Weakens"), so one query missed ~15 weeks.
QUERIES = ("Memory+Spot+Price+Update", "spot+price", "DRAM+spot", "NAND+spot+price")
LINK_RE = re.compile(r"https://www\.trendforce\.com/news/(20\d\d)/(\d\d)/(\d\d)/[^\"']*spot-price[^\"']*/")

# "...DDR4 1Gx8 3200MT/s) has increased by 9.64% from US$25.407 last week (Jan.7) to US$27.857 this week"
DDR4_RE = re.compile(
    r"DDR4\s*1Gx8[^.]*?(?:US\$\s?([\d.]+)[^.]*?)?(?:to|at)\s*US\$\s?([\d.]+)", re.I | re.S
)
DDR4_FLAT_RE = re.compile(r"DDR4\s*1Gx8[^$]*?US\$\s?([\d.]+)", re.I | re.S)
# Preferred: "... from US$5.123 last week to US$5.000 this week" / "holding steady at US$ 46.32".
# The old DDR4_RE can't cross decimals like "-2.40%", which let DDR4_FLAT_RE grab a stray number (2025-08-06 -> 1.53).
_NO_NAND = r"(?:(?!NAND|512Gb|DDR5).){0,300}?"
DDR4_TO_RE = re.compile(r"\b1Gx8(?!\s*reball)" + _NO_NAND + r"\bto\s+US\$\s?([\d.]*\d)", re.I | re.S)
DDR4_AT_RE = re.compile(r"\b1Gx8(?!\s*reball)" + _NO_NAND +
                        r"(?:holding steady at|steady at|arriving at|reaching|remain(?:ed|s)? at|at)\s+US\$\s?([\d.]*\d)", re.I | re.S)
NAND_RE = re.compile(r"512Gb\s*TLC.{0,260}?(?:arriving at|reaching|steady at|to|at)\s*US\$\s?([\d.]+)", re.I | re.S)
PCT_RE = re.compile(r"(increased|decreased|rose|dropped|fell|declined|climbed|up|down)\s+by\s+([\d.]+)%", re.I)


def _num(x: str) -> float | None:
    try:
        return float(x.rstrip("."))
    except ValueError:
        return None


def _get(url: str, retries: int = 3) -> str:
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return ""


def article_links(html: str) -> list[dict]:
    """[{url, date}] for every spot-price article link on a search results page."""
    return [{"url": m.group(0), "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}"} for m in LINK_RE.finditer(html)]


def list_articles(max_pages: int = 30, since: str = "2024-01-01", queries=QUERIES) -> list[dict]:
    """Return [{url, date}] newest first across all search queries, each stopping once older than `since`."""
    out, seen = [], set()
    for query in queries:
        for page in range(1, max_pages + 1):
            html = _get(SEARCH.format(page=page, query=query))
            links = article_links(html) if html else []
            if not links:
                break
            for a in links:
                if a["url"] not in seen:
                    seen.add(a["url"])
                    out.append(a)
            if min(a["date"] for a in links) < since:
                break
            time.sleep(1.0)
    return [a for a in sorted(out, key=lambda a: a["date"], reverse=True) if a["date"] >= since]


def parse_article(url: str) -> dict | None:
    html = _get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("h1") or soup.find("title")).get_text(" ", strip=True)
    art = soup.find("article") or soup
    paras = [p.get_text(" ", strip=True) for p in art.find_all("p")]
    body = " ".join(p for p in paras if p)
    # cut navigation junk after "Get in touch"
    body = body.split("Get in touch with us")[0].strip()

    ddr4 = None
    m = DDR4_TO_RE.search(body) or DDR4_AT_RE.search(body)
    if m:
        ddr4 = _num(m.group(1))
    m = None if ddr4 else DDR4_RE.search(body)
    if m:
        ddr4 = _num(m.group(2))
    # no DDR4_FLAT_RE fallback: it grabbed stray numbers (a missing week beats a wrong one)
    nand = None
    mm = NAND_RE.findall(body)          # take the LAST match = the detail section, not the summary
    if mm:
        nand = _num(mm[-1])
    sp = re.search(r"DDR4\s*1Gx8\s*(\d{4})\s*MT/s", body)
    return {"url": url, "title": title, "body": body, "ddr4_usd": ddr4, "nand_usd": nand,
            "ddr4_speed": sp.group(1) if sp else None}


def crawl(since: str = "2024-01-01", max_pages: int = 120, sleep: float = 1.0) -> tuple[list[dict], list[dict]]:
    """Returns (price_rows, text_rows) ready for schema.upsert_many."""
    price_rows, text_rows = [], []
    for a in list_articles(max_pages=max_pages, since=since):
        p = parse_article(a["url"])
        if not p:
            continue
        date = a["date"]
        if p["ddr4_usd"]:
            price_rows.append({"sku": "TF-DDR4-1GX8", "source": "trendforce_spot", "date": date,
                               "price": p["ddr4_usd"], "currency": "USD",
                               "meta": json.dumps({"url": a["url"], "unit": "per chip, mainstream DDR4 1Gx8",
                                                   "speed_mts": p.get("ddr4_speed")})})
        if p["nand_usd"]:
            price_rows.append({"sku": "TF-NAND-512GB-TLC", "source": "trendforce_spot", "date": date,
                               "price": p["nand_usd"], "currency": "USD",
                               "meta": json.dumps({"url": a["url"], "unit": "per 512Gb TLC wafer"})})
        eid = hashlib.sha1(("trendforce|" + a["url"]).encode()).hexdigest()
        text_rows.append({"event_id": eid, "sku": None, "category": "MARKET",
                          "ts": datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat(),
                          "source": "trendforce", "external_id": a["url"], "url": a["url"],
                          "title": p["title"], "body": p["body"], "lang": "en",
                          "cluster_id": None, "is_canonical": 1, "score_fast": None, "score_judge": None,
                          "meta": json.dumps({"ddr4_usd": p["ddr4_usd"], "nand_usd": p["nand_usd"]})})
        time.sleep(sleep)
    return price_rows, text_rows
