"""r/buildapcsales titles -> retail USD price series (free, back to 2022, no approval).

Titles look like "[SSD] Crucial P3 PCIe Gen3 NVMe 1TB SSD CT1000P3SSD8 - $89.99" or
"[RAM] G.Skill Flare X5 32GB (2x16GB) DDR5 6000 CL30 - $94.99". We parse type, capacity and the
first $ amount, normalise to USD per GB (RAM) / per TB (SSD), and keep the weekly MEDIAN deal so
one mispriced or bundle post can't move the series.

Caveat for the pitch: these are *deal* prices (US retail, skewed to discounts), so the level sits
below list price; the trend is what's comparable with TrendForce chip spot.
"""
from __future__ import annotations

import json
import re

import pandas as pd

PRICE_RE = re.compile(r"\$\s?(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)")
KIT_RE = re.compile(r"(\d)\s*[x×]\s*(\d{1,3})\s*(?:GB|G\b)", re.I)
GB_RE = re.compile(r"(?<![x×\d])(\d{1,3})\s*GB\b", re.I)
TB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*TB\b", re.I)
SSD_GB_RE = re.compile(r"(?<!\d)(\d{3,4})\s*GB\b", re.I)
SSD_SKIP_RE = re.compile(r"\b(portable|external|usb|enclosure|hdd|hard drive|sd card|microsd|flash drive|"
                         r"laptop|prebuilt|desktop pc|mini pc|bundle|combo|motherboard)\b", re.I)
RAM_SKIP_RE = re.compile(r"\b(prebuilt|desktop pc|mini pc|bundle|combo|motherboard|cpu|usb|ssd)\b", re.I)
RAM_BOUNDS = (0.3, 40.0)     # USD per GB
SSD_BOUNDS = (15.0, 1000.0)  # USD per TB


DISCOUNT_AFTER = re.compile(r"\s*(off|discount|rebate|coupon|promo|gift card|credit|back)\b", re.I)
DISCOUNT_BEFORE = re.compile(r"(?:\b(?:save|extra|minus)\s*|\d-)$", re.I)


def _price(title: str) -> float | None:
    """First $ amount that is a price, not a discount ("$10 off", "save $20", "($398.99-$50)")."""
    for m in PRICE_RE.finditer(title):
        if DISCOUNT_AFTER.match(title, m.end()) or DISCOUNT_BEFORE.search(title[:m.start()]):
            continue
        return float(m.group(1).replace(",", ""))
    return None


def parse_ram(title: str) -> dict | None:
    t = re.sub(r"^\s*\[[^\]]*\]", "", title)
    if RAM_SKIP_RE.search(t):
        return None
    kind = (re.search(r"ddr([345])", t, re.I) or [None, None])[1]
    if not kind:
        return None
    caps = [int(a) * int(b) for a, b in KIT_RE.findall(t)] + [int(g) for g in GB_RE.findall(t)]
    caps = [c for c in caps if 4 <= c <= 256]
    price = _price(t)
    if not caps or not price:
        return None
    gb = max(caps)
    per = price / gb
    if not RAM_BOUNDS[0] <= per <= RAM_BOUNDS[1]:
        return None
    return {"type": f"DDR{kind}", "form": "SODIMM" if re.search(r"so-?dimm", t, re.I) else "DIMM",
            "capacity_gb": gb, "price_usd": price, "usd_per_unit": round(per, 4)}


def parse_ssd(title: str) -> dict | None:
    t = re.sub(r"^\s*\[[^\]]*\]", "", title)
    if SSD_SKIP_RE.search(t):
        return None
    caps = [float(x) for x in TB_RE.findall(t)] + [int(g) / 1000 for g in SSD_GB_RE.findall(t) if int(g) >= 120]
    caps = [c for c in caps if 0.12 <= c <= 16]
    price = _price(t)
    if not caps or not price:
        return None
    tb = max(caps)
    per = price / tb
    if not SSD_BOUNDS[0] <= per <= SSD_BOUNDS[1]:
        return None
    iface = ("NVME" if re.search(r"nvme|m\.2|pcie|gen ?[345]", t, re.I)
             else "SATA" if re.search(r"sata|2\.5", t, re.I) else None)
    if not iface:
        return None
    return {"type": iface, "capacity_tb": tb, "price_usd": price, "usd_per_unit": round(per, 2)}


def parse_deals(text: pd.DataFrame) -> pd.DataFrame:
    """text: rows of text_event from reddit:buildapcsales -> one row per parsed deal."""
    out = []
    for r in text.itertuples(index=False):
        p = parse_ram(r.title) if r.category == "RAM" else parse_ssd(r.title) if r.category == "SSD" else None
        if p:
            out.append({"ts": r.ts, "category": r.category, "title": r.title, "url": r.url, **p})
    return pd.DataFrame(out)


SERIES = {  # (category, type) -> price_daily sku
    ("RAM", "DDR5"): "DEAL-RAM-DDR5-USD_PER_GB",
    ("RAM", "DDR4"): "DEAL-RAM-DDR4-USD_PER_GB",
    ("SSD", "NVME"): "DEAL-SSD-NVME-USD_PER_TB",
    ("SSD", "SATA"): "DEAL-SSD-SATA-USD_PER_TB",
}


def weekly_series_rows(deals: pd.DataFrame, min_n: int = 2) -> list[dict]:
    """Weekly median per series, dated at the W-SUN week end; weeks with < min_n deals dropped."""
    if deals.empty:
        return []
    d = deals.copy()
    d["week"] = pd.to_datetime(d["ts"], utc=True).dt.tz_convert(None).dt.to_period("W-SUN").dt.end_time.dt.normalize()
    rows = []
    for (cat, typ), g in d.groupby(["category", "type"]):
        sku = SERIES.get((cat, typ))
        if not sku:
            continue
        for wk, gg in g.groupby("week"):
            if len(gg) < min_n:
                continue
            rows.append({"sku": sku, "source": "reddit_bapcs", "date": wk.strftime("%Y-%m-%d"),
                         "price": float(gg["usd_per_unit"].median()), "currency": "USD",
                         "meta": json.dumps({"n_deals": int(len(gg)), "unit": "USD/GB" if cat == "RAM" else "USD/TB",
                                             "p25": float(gg["usd_per_unit"].quantile(.25)),
                                             "p75": float(gg["usd_per_unit"].quantile(.75))})})
    return rows
