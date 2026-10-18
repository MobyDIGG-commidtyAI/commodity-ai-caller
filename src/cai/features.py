"""How everything merges.

Every source lands in ONE of two long tables with the same keys, so merging is a pivot + join:

  price_daily (sku, source, date, price)      <- TrendForce, yfinance, wiki views, CC retailers, Keepa later
  text_event  (event_id, category, sku, ts)   <- TrendForce narrative, Reddit, HN, GDELT later

Merge rules
  1. Time axis  : everything resampled to WEEKLY (W-SUN last value). Prices forward-filled
                  (a price stays valid until it changes); counts/views summed.
  2. Price side : pivot to wide -> one column per "sku|source". Different currencies are never
                  averaged; THB and USD stay separate columns. Each product SKU gets its proxy
                  series attached from sku_map.trendforce_key + the chip-maker / commodity series.
  3. Text side  : canonical events only (is_canonical=1). Aggregated per (category, week):
                  n_social / n_industry / n_general = canonical events per news genre (genre.py);
                  n_events, n_items (raw incl. dups = attention), mean score_fast / score_judge
                  (NaN until พี่เบิร์ด writes scores back), plus keyword hit counts from keywords.yaml.
                  SKU-level text (sku not null) aggregated separately per sku.
  4. Join key   : (category, week) for market-level text, (sku, week) for product-level text.
  5. Lags       : features_lagged adds shift(1..4) of sentiment so พี่ชิน can test
                  "sentiment this week -> price next k weeks" without leakage.

Output: data/export/features_<CATEGORY>.csv  (one row per week)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
CATEGORY_PROXIES = {   # which market-level series travel with each product category
    "RAM": ["TF-DDR4-1GX8|trendforce_spot", "DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs", "DEAL-RAM-DDR4-USD_PER_GB|reddit_bapcs",
            "EQ-MICRON|yfinance", "EQ-SKHYNIX|yfinance", "EQ-SAMSUNG|yfinance",
            "ATT-WIKI-DDR5|wiki_pageviews", "ATT-WIKI-DDR4|wiki_pageviews", "ATT-WIKI-DRAM|wiki_pageviews", "FX-USDTHB|yfinance",
            "MR-DDR5-USD_PER_GB|memradar", "MR-DDR4-USD_PER_GB|memradar", "MR-DDR5-INDEX|memradar", "MR-DDR4-INDEX|memradar"],
    "SSD": ["TF-NAND-512GB-TLC|trendforce_spot", "DEAL-SSD-NVME-USD_PER_TB|reddit_bapcs", "DEAL-SSD-SATA-USD_PER_TB|reddit_bapcs",
            "EQ-WDC|yfinance", "EQ-SNDK|yfinance", "EQ-KIOXIA|yfinance", "EQ-MICRON|yfinance",
            "ATT-WIKI-SSD|wiki_pageviews", "ATT-WIKI-NVME|wiki_pageviews", "ATT-WIKI-NAND|wiki_pageviews", "FX-USDTHB|yfinance",
            "MR-NVME-USD_PER_GB|memradar", "MR-SATA-USD_PER_GB|memradar"],
    "GPU": ["GPX-GPU-RETAIL-INDEX|gpuprix", "CLOUD-RTX4090-USD_PER_HR|cloud_gpu", "CLOUD-H100-USD_PER_HR|cloud_gpu",
            "CLOUD-A100-USD_PER_HR|cloud_gpu", "EQ-NVIDIA|yfinance", "EQ-AMD|yfinance", "ATT-WIKI-GPU|wiki_pageviews", "ATT-WIKI-RTX50|wiki_pageviews", "FX-USDTHB|yfinance"],
    "COMMODITY": ["CMD-COPPER|yfinance", "CMD-COFFEE|yfinance", "CMD-ALUMINUM|yfinance", "CMD-CRUDE-WTI|yfinance",
                  "ATT-WIKI-COPPER|wiki_pageviews", "ATT-WIKI-COFFEE|wiki_pageviews", "MACRO-US10Y|yfinance"],
}
SPARSE_SOURCES = ("|reddit_bapcs",)


def load_keywords() -> dict[str, list[tuple[str, int]]]:
    cfg = yaml.safe_load((ROOT / "config" / "keywords.yaml").read_text())
    out = {}
    for cat, items in cfg.items():
        if cat == "ASPECTS":
            continue
        out[cat] = [(k.lower(), int(it.get("direction", 0))) for it in items for k in it["kw"]]
    return out


def weekly_prices(price: pd.DataFrame) -> pd.DataFrame:
    price = price.copy()
    price["date"] = pd.to_datetime(price["date"])
    price["key"] = price["sku"] + "|" + price["source"]
    wide = price.pivot_table(index="date", columns="key", values="price", aggfunc="last")
    views = [c for c in wide.columns if c.endswith("wiki_pageviews")]
    w_price = wide.drop(columns=views).resample("W-SUN").last()
    # sparse sources (weekly deal medians) only carry forward 4 weeks; a gap longer than that stays NaN
    sparse = [c for c in w_price.columns if c.endswith(SPARSE_SOURCES)]
    w_price = pd.concat([w_price.drop(columns=sparse).ffill(), w_price[sparse].ffill(limit=4)], axis=1)
    w_views = wide[views].resample("W-SUN").sum(min_count=1) if views else pd.DataFrame(index=w_price.index)
    return w_price.join(w_views, how="outer")


def weekly_text(text: pd.DataFrame, kw: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (by category-week, by sku-week)."""
    t = text.copy()
    t["ts"] = pd.to_datetime(t["ts"], utc=True).dt.tz_convert(None)
    t["week"] = t["ts"].dt.to_period("W-SUN").dt.end_time.dt.normalize()
    t["blob"] = (t["title"].fillna("") + " " + t["body"].fillna("")).str.lower()
    # keyword-direction hits: +1 / -1 / 0 counts per item (cheap rubric baseline, no model needed)
    def kw_dir(row):
        cat = row["category"] if row["category"] in kw else None
        if not cat:
            return 0
        s = 0
        for k, d in kw[cat]:
            if k in row["blob"]:
                s += d
        return max(-1, min(1, s))
    t["kw_dir"] = t.apply(kw_dir, axis=1)
    canon = t[t["is_canonical"] == 1]

    def agg(df, keys):
        g = df.groupby(keys)
        out = pd.DataFrame({
            "n_events": g.size(),
            "kw_dir_mean": g["kw_dir"].mean(),
            "kw_pos": g["kw_dir"].apply(lambda x: (x > 0).sum()),
            "kw_neg": g["kw_dir"].apply(lambda x: (x < 0).sum()),
            "score_fast_mean": g["score_fast"].mean(),
            "score_judge_mean": g["score_judge"].mean(),
        })
        return out
    by_cat = agg(canon, ["category", "week"])
    by_cat["n_items_raw"] = t.groupby(["category", "week"]).size()          # attention incl. duplicates
    if "genre" in canon:                                                     # canonical events per genre
        per = canon.groupby(["category", "week", "genre"]).size().unstack("genre")
        for g, short in (("social_network", "social"), ("industry_news", "industry"), ("general_news", "general")):
            by_cat[f"n_{short}"] = per[g] if g in per else 0
        by_cat[["n_social", "n_industry", "n_general"]] = by_cat[["n_social", "n_industry", "n_general"]].fillna(0)
    by_sku = agg(canon[canon["sku"].notna()], ["sku", "week"])
    return by_cat.reset_index(), by_sku.reset_index()


def build(category: str, price: pd.DataFrame, text: pd.DataFrame, sku_map: pd.DataFrame, lags: int = 4) -> pd.DataFrame:
    kw = load_keywords()
    wp = weekly_prices(price)
    by_cat, by_sku = weekly_text(text, kw)

    cols = [c for c in CATEGORY_PROXIES.get(category, []) if c in wp.columns]
    skus = sku_map[sku_map["category"] == category]["sku"].tolist() if len(sku_map) else []
    prod_cols = [c for c in wp.columns if c.split("|")[0] in skus or c.startswith(f"CC-") and category.lower() in c.lower()]
    # retailer snapshots of mapped skus
    prod_cols += [c for c in wp.columns if c.split("|")[0] in skus]
    prod_cols = sorted(set(prod_cols))
    feat = wp[cols + prod_cols].copy()
    feat.index.name = "week"

    tc = by_cat[by_cat["category"] == category].set_index("week").drop(columns=["category"]).add_prefix("txt_")
    feat = feat.join(tc, how="left")
    txt_cols = [c for c in feat.columns if c.startswith("txt_")]
    extra = {f"{c}_lag{k}": feat[c].shift(k) for k in range(1, lags + 1) for c in txt_cols}
    # forward returns of the headline proxy (target for พี่ชิน's backtest)
    head = cols[0] if cols else None
    if head:
        for k in (1, 2, 4):
            extra[f"target_ret_{k}w"] = feat[head].shift(-k) / feat[head] - 1
    feat = pd.concat([feat, pd.DataFrame(extra, index=feat.index)], axis=1)
    return feat.reset_index()
