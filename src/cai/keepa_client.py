"""Keepa API client (paid, no free tier — plan starts ~€49/mo, 20 tokens/min).

Docs: https://keepa.com/#!discuss/t/product-object/116
Key facts baked in here:
  * 1 token ≈ 1 ASIN with full history. Tokens refill per minute; don't go negative.
  * Keepa time = minutes since 2011-01-01 00:00 UTC  -> epoch_sec = (kt + 21564000) * 60
  * csv[] arrays are [time, value, time, value, ...]; prices in cents (JPY is whole yen); -1 = no data
  * csv index map (subset we use):
      0 AMAZON, 1 NEW (3rd party new), 2 USED, 3 SALES_RANK, 4 LIST_PRICE,
      10 NEW_FBA, 11 COUNT_NEW, 16 RATING(*10), 17 COUNT_REVIEWS,
      18 BUY_BOX_SHIPPING -> triplets [time, price, shipping]
  * domain: 1=com 2=co.uk 3=de 4=fr 5=co.jp 6=ca 8=it 9=es 10=in 11=com.mx

Set KEEPA_KEY env var. Usage:
    kc = KeepaClient()
    asins = kc.search("Crucial DDR5 32GB 5600", domain=1)
    rows  = kc.price_history("B0BMLDQX6V", domain=1, sku="RAM-...", days=730)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import requests

BASE = "https://api.keepa.com"
KEEPA_EPOCH_OFFSET_MIN = 21564000
CSV = {"AMAZON": 0, "NEW": 1, "USED": 2, "SALES_RANK": 3, "LIST_PRICE": 4,
       "NEW_FBA": 10, "COUNT_NEW": 11, "RATING": 16, "COUNT_REVIEWS": 17, "BUY_BOX": 18}
SOURCE_NAME = {"AMAZON": "keepa_amazon", "NEW": "keepa_new", "NEW_FBA": "keepa_new_fba", "BUY_BOX": "keepa_buybox"}
CURRENCY = {1: "USD", 2: "GBP", 3: "EUR", 4: "EUR", 5: "JPY", 6: "CAD", 8: "EUR", 9: "EUR", 10: "INR", 11: "MXN"}


def keepa_min_to_date(kt: int) -> str:
    return datetime.fromtimestamp((kt + KEEPA_EPOCH_OFFSET_MIN) * 60, tz=timezone.utc).strftime("%Y-%m-%d")


class KeepaClient:
    def __init__(self, key: str | None = None, min_tokens: int = 5):
        self.key = key or os.environ.get("KEEPA_KEY")
        if not self.key:
            raise RuntimeError("KEEPA_KEY env var not set")
        self.tokens_left = None
        self.refill_in_ms = None
        self.min_tokens = min_tokens

    # ---------- low level ----------
    def _get(self, path: str, **params) -> dict:
        self._wait_for_tokens()
        params["key"] = self.key
        r = requests.get(f"{BASE}/{path}", params=params, timeout=60)
        data = r.json()
        self.tokens_left = data.get("tokensLeft", self.tokens_left)
        self.refill_in_ms = data.get("refillIn", self.refill_in_ms)
        if r.status_code != 200 or "error" in data:
            raise RuntimeError(f"Keepa {path} failed: {r.status_code} {data.get('error')}")
        return data

    def _wait_for_tokens(self):
        if self.tokens_left is not None and self.tokens_left < self.min_tokens and self.refill_in_ms:
            time.sleep(self.refill_in_ms / 1000 + 1)

    # ---------- public ----------
    def search(self, term: str, domain: int = 1, limit: int = 10) -> list[dict]:
        """Keyword -> candidate ASINs (title, asin). Costs ~10 tokens. Pick the right ASIN by hand into sku_map.yaml."""
        data = self._get("search", domain=domain, type="product", term=term)
        out = []
        for p in data.get("products", [])[:limit]:
            out.append({"asin": p.get("asin"), "title": p.get("title"), "brand": p.get("brand")})
        return out

    def products(self, asins: list[str], domain: int = 1, days: int = 730) -> list[dict]:
        """Raw product objects with history limited to `days` (0 = everything). Batch up to 100 ASINs."""
        out = []
        for i in range(0, len(asins), 100):
            batch = ",".join(asins[i:i + 100])
            data = self._get("product", domain=domain, asin=batch, history=1, days=days, stats=90, buybox=1)
            out.extend(data.get("products", []))
        return out

    @staticmethod
    def decode_series(csv_arr: list | None, triplet: bool = False) -> dict[str, float]:
        """Keepa [t,v,t,v...] (or [t,v,ship,...]) -> {date: last value of that day}. Drops -1."""
        if not csv_arr:
            return {}
        step = 3 if triplet else 2
        series: dict[str, float] = {}
        for i in range(0, len(csv_arr) - 1, step):
            t, v = csv_arr[i], csv_arr[i + 1]
            if v is None or v < 0:
                continue
            series[keepa_min_to_date(t)] = v
        return series

    @staticmethod
    def forward_fill_daily(series: dict[str, float], end_date: str | None = None) -> dict[str, float]:
        """Keepa only stores change-points; expand to one value per calendar day."""
        if not series:
            return {}
        import pandas as pd
        s = pd.Series(series)
        s.index = pd.to_datetime(s.index)
        s = s.sort_index()
        end = pd.Timestamp(end_date) if end_date else pd.Timestamp.utcnow().normalize().tz_localize(None)
        idx = pd.date_range(s.index.min(), end, freq="D")
        s = s.reindex(idx).ffill()
        return {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}

    def price_history(self, asin: str, domain: int, sku: str, days: int = 730,
                      fields: tuple[str, ...] = ("AMAZON", "NEW", "BUY_BOX")) -> list[dict]:
        """-> price_daily rows for one ASIN (forward-filled daily, in major currency units)."""
        prods = self.products([asin], domain=domain, days=days)
        if not prods or prods[0].get("csv") is None:
            return []
        p = prods[0]
        cur = CURRENCY.get(domain, "USD")
        divisor = 1 if cur == "JPY" else 100
        rows = []
        for f in fields:
            arr = p["csv"][CSV[f]] if len(p["csv"]) > CSV[f] else None
            series = self.decode_series(arr, triplet=(f == "BUY_BOX"))
            for d, v in self.forward_fill_daily(series).items():
                rows.append({"sku": sku, "source": SOURCE_NAME[f], "date": d, "price": v / divisor,
                             "currency": cur, "meta": json.dumps({"asin": asin, "domain": domain})})
        return rows
