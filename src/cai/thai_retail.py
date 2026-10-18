"""Daily THB price snapshot from Thai IT retailers.

No Thai source has a public price-history API, so this only captures from the day
you start running it (cron daily). Purpose: build an overlap window to prove that
Keepa/TrendForce series are a usable proxy for Thai retail prices.

Config-driven: each sku in sku_map.yaml can carry `thai_urls: {jib: url, advice: url, ...}`.
Selectors live in RETAILERS; adjust when a site changes. Playwright fallback for JS-rendered pages.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Accept-Language": "th-TH,th;q=0.9,en;q=0.8"}

# CSS selectors are best-effort; verify on first run and fix per site.
RETAILERS = {
    "jib":    {"price": ["span.price", ".product-price", "[itemprop=price]"]},
    "advice": {"price": [".sale-price", ".price", "[itemprop=price]"]},
    "banana": {"price": [".product-price", ".price", "[itemprop=price]"]},
    "shopee": {"price": []},   # JS-rendered + anti-bot -> use playwright or descope
}
PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,6})(?:\.\d+)?")


def _extract_price(html: str, selectors: list[str]) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    for sel in selectors:
        for el in soup.select(sel):
            txt = el.get("content") or el.get_text(" ", strip=True)
            m = PRICE_RE.search(txt.replace("฿", ""))
            if m:
                return float(m.group(1).replace(",", ""))
    # fallback: JSON-LD offers.price
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            offers = obj.get("offers") if isinstance(obj, dict) else None
            if isinstance(offers, dict) and offers.get("price"):
                return float(str(offers["price"]).replace(",", ""))
    return None


def snapshot(sku: str, retailer: str, url: str) -> dict | None:
    try:
        r = requests.get(url, headers=UA, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    price = _extract_price(r.text, RETAILERS.get(retailer, {}).get("price", []))
    if price is None:
        return None
    return {"sku": sku, "source": retailer, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "price": price, "currency": "THB", "meta": json.dumps({"url": url})}
