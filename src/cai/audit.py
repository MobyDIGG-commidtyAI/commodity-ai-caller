"""Completeness audit over data/export/ — what we have, how fresh it is, and where the holes are.
Works on the exported files only (no tank.db needed), so the same code runs in Colab.
usage: python -m cai.audit  (or import and call `run(export_dir)`)"""
import pathlib
import pandas as pd
import pyarrow.parquet

EXPORT = pathlib.Path(__file__).resolve().parents[2] / "data" / "export"

# expected cadence per source, in days: a gap longer than ~2 cadences is reported as a hole
CADENCE = {"trendforce_spot": 7, "reddit_bapcs": 7, "yfinance": 1, "wiki_pageviews": 1,
           "memradar": 31, "gpuprix": 31, "cloud_gpu": 7}
EXPECTED_STALE = {   # known, explained staleness — reported, but not a collection failure
    "DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs": "RAM deals mostly vanished since 2026-05 (fewer than 2 per week)",
    "DEAL-RAM-DDR4-USD_PER_GB|reddit_bapcs": "RAM deals mostly vanished since 2026-05 (fewer than 2 per week)",
}
HEADLINE = ["TF-DDR4-1GX8|trendforce_spot", "TF-NAND-512GB-TLC|trendforce_spot",
            "DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs", "DEAL-RAM-DDR4-USD_PER_GB|reddit_bapcs",
            "DEAL-SSD-NVME-USD_PER_TB|reddit_bapcs", "DEAL-SSD-SATA-USD_PER_TB|reddit_bapcs"]


def load(export_dir=EXPORT) -> dict:
    d = pathlib.Path(export_dir)
    price = pd.read_parquet(d / "price_daily.parquet")
    price["date"] = pd.to_datetime(price["date"])
    text = pd.read_parquet(d / "text_event.parquet", columns=["source", "category", "ts", "is_canonical"])
    text["ts"] = pd.to_datetime(text["ts"], utc=True, format="mixed").dt.tz_localize(None)
    feats = {c: pd.read_csv(d / f"features_{c}.csv", parse_dates=["week"]) for c in ("RAM", "SSD", "GPU", "COMMODITY")}
    return {"price": price, "text": text, "features": feats}


def price_coverage(price: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for (sku, src), g in price.groupby(["sku", "source"]):
        dates = g["date"].sort_values()
        gaps = dates.diff().dt.days
        cadence = CADENCE.get(src, 7)
        slack = 4 if cadence == 1 else 0   # daily markets skip weekends and holidays
        holes = gaps[gaps > 2 * cadence + slack]
        key = f"{sku}|{src}"
        stale = (asof - dates.iloc[-1]).days
        rows.append({"series": key, "first": dates.iloc[0].date(), "last": dates.iloc[-1].date(), "n": len(dates),
                     "max_gap_days": int(gaps.max()) if len(gaps) > 1 else 0, "n_holes": len(holes),
                     "days_stale": stale,
                     "status": "known" if key in EXPECTED_STALE and stale > 3 * cadence + slack
                               else ("STALE" if stale > 3 * cadence + slack else "ok"),
                     "note": EXPECTED_STALE.get(key, "")})
    return pd.DataFrame(rows).sort_values(["status", "series"]).reset_index(drop=True)


def text_coverage(text: pd.DataFrame, since: str = "2025-01-01") -> pd.DataFrame:
    rows = []
    for src, g in text.groupby("source"):
        w = g[g["ts"] >= since].set_index("ts").resample("W-SUN").size()
        rows.append({"source": src, "first": g["ts"].min().date(), "last": g["ts"].max().date(), "n": len(g),
                     "canonical": int(g["is_canonical"].sum()),
                     f"weeks_since_{since[:4]}": len(w), "median_per_week": float(w.median()) if len(w) else 0.0,
                     "zero_weeks": int((w == 0).sum())})
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)


def weekly_events(text: pd.DataFrame, since: str = "2025-01-01") -> pd.DataFrame:
    """Canonical RAM/SSD items per week, split posts vs comments (comments come in bursts of up to 100)."""
    t = text[(text["is_canonical"] == 1) & (text["ts"] >= since) & text["category"].isin(["RAM", "SSD"])].copy()
    t["kind"] = t["source"].str.startswith("reddit_c:").map({True: "comments", False: "posts"})
    return t.groupby(["category", "kind"]).resample("W-SUN", on="ts").size().unstack(["category", "kind"]).fillna(0).astype(int)


def criteria(data: dict, asof: pd.Timestamp) -> pd.DataFrame:
    price, feats = data["price"], data["features"]
    keys = price["sku"] + "|" + price["source"]
    out = []
    # (a) weekly chip series 2024 -> now
    for k in HEADLINE[:2]:
        d = price.loc[keys == k, "date"]
        gaps = d[d >= "2024-01-01"].sort_values().diff().dt.days.max()
        ok = d.min() <= pd.Timestamp("2024-01-07") and (asof - d.max()).days <= 14 and gaps <= 14
        out.append(("a", f"weekly chip series 2024→now: {k.split('|')[0]}", ok,
                    f"{d.min().date()} → {d.max().date()}, max gap since 2024 {int(gaps)} d"))
    # (b) retail USD series with >= 12 monthly points
    for k in HEADLINE[2:]:
        d = price.loc[keys == k, "date"]
        months = d.dt.to_period("M").nunique()
        out.append(("b", f"retail series ≥12 monthly points: {k.split('|')[0]}", months >= 12, f"{months} months"))
    # (c) >= 50 canonical events / week since 2025, per category
    we = weekly_events(data["text"])
    for cat in ("RAM", "SSD"):
        tot = we[cat].sum(axis=1)
        posts = we[(cat, "posts")]
        out.append(("c", f"≥50 canonical events/week since 2025: {cat}", bool(tot.median() >= 50),
                    f"median {tot.median():.0f} (posts only {posts.median():.0f}); "
                    f"{int((tot < 50).sum())}/{len(tot)} weeks below 50"))
    # (d) no all-NaN text columns except the reserved model-score slots
    for cat in ("RAM", "SSD"):
        f = feats[cat][feats[cat]["week"] >= "2025-01-01"]
        bad = [c for c in f.columns if c.startswith("txt_") and f[c].isna().all() and "score_" not in c]
        out.append(("d", f"no empty text columns (except score slots): {cat}", not bad, ", ".join(bad) or "none"))
    return pd.DataFrame(out, columns=["criterion", "check", "pass", "evidence"])


def export_roundtrip(export_dir=EXPORT) -> pd.DataFrame:
    d = pathlib.Path(export_dir)
    pairs = [("price_daily", d / "price_daily.parquet", d / "price_daily.csv"),
             ("text_event", d / "text_event.parquet", d / "text_event.csv.gz"),
             ("sku_map", d / "sku_map.parquet", d / "sku_map.csv")]
    rows = []
    for name, pq, csv in pairs:
        a = pyarrow.parquet.ParquetFile(pq).metadata.num_rows
        b = len(pd.read_csv(csv, usecols=[0]))
        rows.append({"table": name, "parquet_rows": a, "csv_rows": b, "match": a == b,
                     "largest_file_mb": round(max(pq.stat().st_size, csv.stat().st_size) / 1e6, 1)})
    return pd.DataFrame(rows)


def run(export_dir=EXPORT, asof=None) -> dict:
    data = load(export_dir)
    asof = pd.Timestamp(asof or pd.Timestamp.today().normalize())
    return {"criteria": criteria(data, asof), "price": price_coverage(data["price"], asof),
            "text": text_coverage(data["text"]), "roundtrip": export_roundtrip(export_dir)}


if __name__ == "__main__":
    pd.set_option("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 80)
    for name, df in run().items():
        print(f"\n== {name}\n{df.to_string()}")
