"""Common Crawl as a free price-history archive (works where web.archive.org is blocked).

Each monthly crawl is a snapshot of the web. For a retailer product URL prefix we:
  1. query index.commoncrawl.org/<crawl>-index  (pywb CDX; often 503/504 under load -> heavy retry)
  2. fetch only the byte range of that page from data.commoncrawl.org (WARC, gzip)
  3. extract product name + price (JSON-LD -> meta -> CSS -> baht regex)
  4. one price_daily row per (sku_guess, retailer, crawl_date)

Resolution ≈ monthly. Coverage depends on whether CC crawled that page that month.
Auto-maps page -> sku via keywords in config/sku_map.yaml; unmapped pages still saved
with sku = "CC-<retailer>-<slug>" so nothing is thrown away (เยอะ ๆ ไว้ก่อน).
"""
from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "commodity-ai-hackathon/0.1 (research)"}
INDEX = "https://index.commoncrawl.org/{crawl}-index"
DATA = "https://data.commoncrawl.org/"

# retailer -> (url prefix for CDX, regex a product URL must match to be kept)
RETAILERS = {
    "jib":    ("www.jib.co.th/web/product/readProduct/*",  r"(?i)ddr[345]|ssd|nvme|m\.2|ram|memory|harddisk|hdd|rtx|radeon|geforce"),
    "advice": ("www.advice.co.th/product/*",                r"(?i)ddr[345]|ssd|nvme|ram|memory|hdd|rtx|radeon"),
    "banana": ("www.bnn.in.th/*/p/*",                       r"(?i)ddr[345]|ssd|nvme|ram|memory|hdd|rtx|radeon"),
    "amazon": ("www.amazon.com/*/dp/*",                     r"(?i)ddr[345]|ssd|nvme|ram-memory|memory|rtx|radeon"),
}
PRICE_RE = re.compile(r"(?:฿|THB|บาท|\$)\s?([\d,]{3,9})(?:\.\d+)?|([\d,]{4,9})\s?(?:บาท|THB)")


def discover_crawls(candidates: list[str] | None = None) -> list[str]:
    """collinfo.json is flaky; probe candidate ids instead. Returns ids that answer 200."""
    try:
        r = requests.get("https://index.commoncrawl.org/collinfo.json", headers=UA, timeout=30)
        if r.status_code == 200:
            return [c["id"] for c in r.json()]
    except Exception:
        pass
    # fallback: CC names crawls by ISO week; generate plausible ids for the last ~30 months
    if candidates is None:
        candidates = []
        now = datetime.utcnow()
        for y in range(now.year, now.year - 3, -1):
            for w in range(52, 0, -1):
                candidates.append(f"CC-MAIN-{y}-{w:02d}")
    ok = []
    for c in candidates:
        try:
            r = requests.get(INDEX.format(crawl=c), params={"url": "www.jib.co.th", "output": "json", "limit": 1},
                             headers=UA, timeout=40)
            if r.status_code == 200:
                ok.append(c)
        except Exception:
            pass
        time.sleep(0.5)
    return ok


def cdx_query(crawl: str, url_pattern: str, limit: int = 2000, retries: int = 6) -> list[dict]:
    """pywb CDX; retries hard because the public index server is frequently overloaded."""
    for i in range(retries):
        try:
            r = requests.get(INDEX.format(crawl=crawl), params={"url": url_pattern, "output": "json", "limit": limit,
                                                                  "filter": "=status:200"}, headers=UA, timeout=120)
            if r.status_code == 200:
                return [json.loads(l) for l in r.text.splitlines() if l.startswith("{")]
            if r.status_code == 404:
                return []
        except requests.RequestException:
            pass
        time.sleep(10 * (i + 1))
    return []


def fetch_warc_html(rec: dict, retries: int = 3) -> str:
    off, ln = int(rec["offset"]), int(rec["length"])
    for i in range(retries):
        try:
            r = requests.get(DATA + rec["filename"], headers={**UA, "Range": f"bytes={off}-{off + ln - 1}"}, timeout=90)
            if r.status_code in (200, 206):
                raw = gzip.decompress(r.content).decode("utf-8", "ignore")
                return raw.split("\r\n\r\n", 2)[-1]          # WARC header, HTTP header, body
        except Exception:
            pass
        time.sleep(3 * (i + 1))
    return ""


def extract_price(html: str) -> tuple[str, float | None, str]:
    """-> (title, price, currency). Order: JSON-LD offers -> og/meta -> itemprop -> text regex."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("meta", property="og:title") or {}).get("content") or (soup.title.string if soup.title else "") or ""
    title = title.strip()[:200]
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for o in objs:
            if not isinstance(o, dict):
                continue
            if "@graph" in o:
                objs.extend(x for x in o["@graph"] if isinstance(x, dict))
            offers = o.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict) and offers.get("price"):
                try:
                    return title, float(str(offers["price"]).replace(",", "")), offers.get("priceCurrency", "THB")
                except ValueError:
                    pass
    m = soup.find("meta", property="product:price:amount") or soup.find("meta", attrs={"itemprop": "price"})
    if m and m.get("content"):
        cur = (soup.find("meta", property="product:price:currency") or {}).get("content", "THB")
        try:
            return title, float(m["content"].replace(",", "")), cur
        except ValueError:
            pass
    el = soup.select_one("[itemprop=price]")
    if el:
        mm = PRICE_RE.search(el.get("content") or el.get_text(" ", strip=True))
        if mm:
            return title, float((mm.group(1) or mm.group(2)).replace(",", "")), "THB"
    text = soup.get_text(" ", strip=True)[:20000]
    mm = PRICE_RE.search(text)
    if mm:
        return title, float((mm.group(1) or mm.group(2)).replace(",", "")), "USD" if "$" in mm.group(0) else "THB"
    return title, None, "THB"


def map_sku(title: str, url: str, sku_cfg: list[dict]) -> str | None:
    t = f"{title} {url}".lower()
    for s in sku_cfg:
        if all(k.lower() in t for k in s["keywords"][:1]):        # first keyword = the model identifier
            return s["sku"]
    return None


def crawl_retailer(crawl: str, retailer: str, sku_cfg: list[dict], max_pages: int = 150, sleep: float = 0.7) -> list[dict]:
    prefix, keep_re = RETAILERS[retailer]
    recs = cdx_query(crawl, prefix)
    recs = [r for r in recs if re.search(keep_re, r["url"])][:max_pages]
    rows = []
    for r in recs:
        html = fetch_warc_html(r)
        if not html:
            continue
        title, price, cur = extract_price(html)
        if price is None or price < 100:
            continue
        sku = map_sku(title, r["url"], sku_cfg) or f"CC-{retailer}-" + re.sub(r"[^a-z0-9]+", "-", r["url"].rsplit("/", 1)[-1].lower())[:60]
        ts = r["timestamp"]
        rows.append({"sku": sku, "source": f"cc_{retailer}", "date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", "price": price,
                     "currency": cur, "meta": json.dumps({"url": r["url"], "crawl": crawl, "title": title[:120]})})
        time.sleep(sleep)
    return rows
