"""Import พี่ชิน's processed archive (hardware-news-price-latest, 2026-10-15) into the tank.

The archive is kept as-is under data/partner/chin-2026-10-15/ (README, MANIFEST and provenance included).
This module only maps the parts that fit the tank's two long tables:

  news/articles.csv                          -> text_event, source "chin:<source_dataset>", titles only
  prices/memradar-price-index-monthly.csv    -> price_daily MR-<SEG>-USD_PER_GB|memradar (monthly level)
  prices/retail-and-cloud-price-contract.json
      ram.ddr4/ddr5[].index                  -> MR-DDR4-INDEX / MR-DDR5-INDEX|memradar (monthly change measure)
      gpuRetail[].index                      -> GPX-GPU-RETAIL-INDEX|gpuprix (monthly)
      gpuWeekly.<slug>[].medianUsdPerHour    -> CLOUD-<SLUG>-USD_PER_HR|cloud_gpu (weekly)

Monthly values are dated at month END: a month's median is only known once the month closes, so a
month-start date would leak into forward-return targets.

Not imported on purpose:
  thai-retail-snapshot.csv  - observed_at is blank (dataset card says 2023); writing a date would invent one
  weekly/monthly signals, news-indicators, keyword-impact - พี่ชิน's derived outputs, read them from the archive
  gpu-cloud-hourly/weekly raw files - 100+ GPU slugs x billing types; the contract already selects the series
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pandas as pd

MR_SEGMENTS = {"ddr5": "DDR5", "ddr4": "DDR4", "nvme_ssd": "NVME", "sata_ssd": "SATA"}
CATEGORY = {"gpu": "GPU", "ram": "RAM"}
SURVIVORSHIP = "catalog assembled 2026-07: history covers only products still sold in 2026"


def month_end(month: str) -> str:
    """'2026-09' -> '2026-09-30'."""
    return (pd.Period(month, freq="M").end_time.normalize()).strftime("%Y-%m-%d")


def _eid(source: str, ext: str) -> str:
    return hashlib.sha1(f"{source}|{ext}".encode()).hexdigest()


def article_rows(path: Path) -> list[dict]:
    """news/articles.csv -> text_event rows. Half the rows have no URL and some URLs repeat,
    so the id is built from dataset + timestamp + title, not the URL."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for a in csv.DictReader(f):
            cat = CATEGORY.get(a["product"].strip().lower())
            if not cat or not a["title"].strip():
                continue
            ts = pd.Timestamp(a["published_at"])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            source = f"chin:{a['source_dataset']}"
            ext = hashlib.sha1(f"{a['source_dataset']}|{ts_iso}|{a['title'].strip()}".encode()).hexdigest()[:16]
            meta = {"publisher": a["source"] or None,
                    "chin_sentiment": float(a["sentiment"]) if a["sentiment"] else None,
                    "chin_price_pressure": float(a["price_pressure"]) if a["price_pressure"] else None,
                    "has_up_signal": int(a["has_up_signal"] or 0), "has_down_signal": int(a["has_down_signal"] or 0)}
            rows.append({"event_id": _eid(source, ext), "sku": None, "category": cat, "ts": ts_iso,
                         "source": source, "external_id": ext, "url": a["url"] or None, "title": a["title"].strip(),
                         "body": None, "lang": "en", "meta": json.dumps(meta)})
    # same dataset + second + title twice in the file -> one event
    return list({r["event_id"]: r for r in rows}.values())


def memradar_rows(path: Path) -> list[dict]:
    """MemRadar monthly CSV (has '#' comment lines) -> USD-per-GB level per segment."""
    text = "".join(line for line in open(path, encoding="utf-8") if not line.startswith("#"))
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        seg = MR_SEGMENTS.get(r["segment"])
        if not seg or not r["median_usd_per_gb"]:
            continue
        rows.append({"sku": f"MR-{seg}-USD_PER_GB", "source": "memradar", "date": month_end(r["month"]),
                     "price": float(r["median_usd_per_gb"]), "currency": "USD",
                     "meta": json.dumps({"median_price_usd": float(r["median_price_usd"]),
                                         "product_count": int(r["product_count"]), "note": SURVIVORSHIP})})
    return rows


def contract_rows(path: Path) -> list[dict]:
    """retail-and-cloud-price-contract.json -> MemRadar index, GpuPrix index, cloud GPU weekly medians."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    for gen, pts in d.get("ram", {}).items():
        for p in pts:
            rows.append({"sku": f"MR-{gen.upper()}-INDEX", "source": "memradar", "date": month_end(p["month"]),
                         "price": float(p["index"]), "currency": "INDEX",
                         "meta": json.dumps({"usd_per_gb": p.get("usdPerGb"), "products": p.get("products")})})
    for p in d.get("gpuRetail", []):
        rows.append({"sku": "GPX-GPU-RETAIL-INDEX", "source": "gpuprix", "date": month_end(p["month"]),
                     "price": float(p["index"]), "currency": "INDEX",
                     "meta": json.dumps({"matched_models": p.get("matchedModels")})})
    for slug, pts in d.get("gpuWeekly", {}).items():
        name = slug.removeprefix("nvidia-").replace("-", "").upper()
        for p in pts:
            rows.append({"sku": f"CLOUD-{name}-USD_PER_HR", "source": "cloud_gpu", "date": p["date"][:10],
                         "price": float(p["medianUsdPerHour"]), "currency": "USD",
                         "meta": json.dumps({"provider_median": p.get("providerMedianUsdPerHour"),
                                             "providers": p.get("providers"), "gpu_slug": slug})})
    return rows


def load(archive: Path) -> tuple[list[dict], list[dict]]:
    """archive = the extracted hardware-news-price-latest/ folder -> (price rows, text rows)."""
    archive = Path(archive)
    price = memradar_rows(archive / "prices" / "memradar-price-index-monthly.csv")
    price += contract_rows(archive / "prices" / "retail-and-cloud-price-contract.json")
    return price, article_rows(archive / "news" / "articles.csv")
