"""Versioned, offline-built shopping evidence. Runtime never fits a model.

All evaluation targets are observations; interpolated display histories are kept
separate. Dates are source dates, not a claim about publication availability.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from cai.product import parse_title, GPU_RE, RAM_GEN_RE, VALID_GB
from cai.deals import _price

SCHEMA_VERSION = 2
REFERENCE_LABEL = "US reference converted to THB"
UNITS = {"RAM": "USD/GB", "SSD": "USD/GB", "GPU": "USD/card"}
BAD_CONDITION = re.compile(
    r"\b(used|refurb\w*|renewed|reconditioned|open[\s-]?box|like[\s-]?new|"
    r"resale|warehouse|bundle|combo|scam|pricing error|price error|enclosure|"
    r"prebuilt|laptop|notebook|waterblock)\b", re.I
)
MULTI_VARIANT = re.compile(r"\d+\s*(?:GB|TB)\s*[-|/]\s*\d+\s*(?:GB|TB)", re.I)
SEGMENTS = {
    "DDR4": ("RAM", "RAM_DDR4", "MR-DDR4-USD_PER_GB", "TF-DDR4-1GX8", 28),
    "DDR5": ("RAM", "RAM_DDR5", "MR-DDR5-USD_PER_GB", "TF-DDR4-1GX8", 14),
    "NVME": ("SSD", "SSD_NVME", "MR-NVME-USD_PER_GB", "TF-NAND-512GB-TLC", 42),
    "SATA": ("SSD", "SSD_SATA", "MR-SATA-USD_PER_GB", "TF-NAND-512GB-TLC", 42),
}
GPU_MODELS = set(
    ("GTX 1050 TI,GTX 1060,GTX 1070,GTX 1070 TI,GTX 1080,GTX 1080 TI,"
     "GTX 1630,GTX 1650,GTX 1650 SUPER,GTX 1660,GTX 1660 TI,GTX 1660 SUPER,"
     "RTX 2060,RTX 2060 SUPER,RTX 2070,RTX 2070 SUPER,RTX 2080,RTX 2080 SUPER,RTX 2080 TI,"
     "RTX 3050,RTX 3060,RTX 3060 TI,RTX 3070,RTX 3070 TI,RTX 3080,RTX 3080 TI,RTX 3090,RTX 3090 TI,"
     "RTX 4060,RTX 4060 TI,RTX 4070,RTX 4070 TI,RTX 4070 SUPER,RTX 4070 TI SUPER,RTX 4080,RTX 4080 SUPER,RTX 4090,"
     "RTX 5050,RTX 5060,RTX 5060 TI,RTX 5070,RTX 5070 TI,RTX 5080,RTX 5090,"
     "RX 550,RX 5500 XT,RX 5600,RX 5600 XT,RX 5700,RX 5700 XT,RX 6400,RX 6500 XT,"
     "RX 6600,RX 6600 XT,RX 6650 XT,RX 6700,RX 6700 XT,RX 6750 XT,RX 6800,RX 6800 XT,RX 6900 XT,RX 6950 XT,"
     "RX 7600,RX 7600 XT,RX 7700 XT,RX 7800 XT,RX 7900 GRE,RX 7900 XT,RX 7900 XTX,"
     "RX 9060 XT,RX 9070,RX 9070 XT").split(",")
)


def today() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Asia/Bangkok").normalize().tz_localize(None)


def iso(d) -> str:
    return pd.Timestamp(d).date().isoformat()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    return value


def dated(series: pd.Series) -> list[dict]:
    return [{"date": iso(d), "value": float(v)} for d, v in series.items()]


def observations(rows: list[dict]) -> pd.Series:
    if not rows:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return pd.Series([r["value"] for r in rows], index=pd.to_datetime([r["date"] for r in rows])).sort_index()


def ambiguous_title(title):
    return bool(MULTI_VARIANT.search(title) or len({m[0].upper() for m in GPU_RE.finditer(title)}) > 1
                or len({m[1] for m in RAM_GEN_RE.finditer(title)}) > 1
                or (GPU_RE.search(title) and re.search(r"/\s*(?:RTX\s*)?\d{4}", title, re.I)))


def source_series(df: pd.DataFrame, sku: str, asof) -> pd.Series:
    q = df.loc[df.sku.eq(sku) & (df.date <= asof)]
    q = q.loc[np.isfinite(q.price) & q.price.gt(0)]
    return q.groupby("date").price.median().sort_index()


def seasonal(obs: pd.Series, asof) -> dict:
    """Calendar residual profile; no filled observations count toward eligibility."""
    obs = obs.loc[obs.index <= asof]
    result = {"status": "historical_only", "label": "No clear seasonal pattern",
              "reason": "Not enough repeatable, observed calendar history.",
              "profile": [], "years": 0, "validation": [], "method": "Within-year log-linear detrending"}
    if len(obs) < 12:
        return result
    t = obs.rename("price").to_frame()
    t["year"], t["month"] = t.index.year, t.index.month
    m = t.groupby(["year", "month"]).price.median().reset_index()
    profiles = []
    for year, g in m.groupby("year"):
        if len(g) < 6:
            continue
        b, a = np.polyfit(g.month, np.log(g.price), 1)
        residual = 100 * np.exp(np.log(g.price) - (a + b * g.month))
        profiles.append(pd.Series(residual.values, index=g.month.values, name=int(year)))
    if not profiles:
        return result
    matrix = pd.concat(profiles, axis=1).reindex(range(1, 13))
    profile = matrix.median(axis=1)
    result["profile"] = [{"month": i, "index": clean(profile.loc[i]),
                          "years": int(matrix.loc[i].count())} for i in range(1, 13)]
    result["years"] = len(profiles)
    eligible = matrix.loc[:, matrix.count().ge(9)]
    tests = []
    if eligible.shape[1] >= 2:
        for year in eligible:
            reference = eligible.drop(columns=year).median(axis=1)
            actual = eligible[year]
            ok = actual.notna() & reference.notna()
            variable = actual[ok].std() > 1e-8 and reference[ok].std() > 1e-8
            corr = actual[ok].corr(reference[ok]) if ok.sum() >= 9 and variable else np.nan
            tests.append({"year": int(year), "months": int(ok.sum()), "correlation": clean(corr)})
    result["validation"] = tests
    # Require agreement in every held-out year; no group assignment from full data.
    if tests and all(r["correlation"] is not None and r["correlation"] >= .5 for r in tests):
        if profile.notna().sum() == 12 and profile.max() / profile.min() >= 1.05:
            result["status"] = "supported"
            result["reason"] = "At least two qualifying years agree in leave-one-year-out checks."
    return result


def _forecast(obs: pd.Series, days: int, method: str, season: dict | None = None) -> float:
    last = float(obs.iloc[-1])
    if method == "carry":
        return last
    recent = obs.loc[obs.index >= obs.index[-1] - pd.Timedelta(days=180)]
    if len(recent) < 6:
        return last
    x = (recent.index - recent.index[0]).days.to_numpy()
    slope = float(np.polyfit(x, np.log(recent.values), 1)[0]) if np.ptp(x) > 0 else 0.
    # Half-life damping limits extrapolation of commodity price shocks.
    log_change = np.clip(slope * 45 * (1 - np.exp(-days / 45)), -np.log(1.5), np.log(1.5))
    value = last * np.exp(log_change)
    if method == "seasonal" and season and season["status"] == "supported":
        p = {r["month"]: r["index"] for r in season["profile"]}
        end = obs.index[-1] + pd.Timedelta(days=days)
        value *= (p[end.month] / p[obs.index[-1].month]) ** .5
    return float(value)


def outlook(obs: pd.Series, asof) -> dict:
    """Monthly rolling origins; select candidates from completed earlier outcomes only."""
    obs = obs.loc[obs.index <= asof]
    result = {"status": "unavailable", "label": "Uncertain", "reason": "Not enough recent evidence",
              "points": [], "validation": [], "method": None, "confidence": "none"}
    if len(obs) < 6 or len(obs.loc[obs.index >= asof - pd.Timedelta(days=180)]) < 6:
        return result
    methods = ["carry", "trend", "seasonal"]
    # One origin per calendar month prevents daily carry rows inflating sample size.
    origins = obs.groupby(obs.index.to_period("M")).tail(1).index
    evaluations = []
    final_method = "trend"
    final_errors = []
    for horizon in (30, 60, 90):
        records = []
        for origin in origins:
            prefix = obs.loc[obs.index <= origin]
            if len(prefix) < 12 or (prefix.index[-1] - prefix.index[0]).days < 180:
                continue
            nominal = origin + pd.Timedelta(days=horizon)
            distance = np.abs((obs.index - nominal).days)
            j = int(distance.argmin())
            actual_date = obs.index[j]
            if distance[j] > 7 or actual_date <= origin or actual_date > asof:
                continue
            s = seasonal(prefix, origin)
            candidates = {name: _forecast(prefix, (actual_date - origin).days, name, s)
                          for name in methods if name != "seasonal" or s["status"] == "supported"}
            past = [r for r in records if r["target_date"] <= origin]
            available = [name for name in candidates if name != "carry"]
            selected = min(available, key=lambda name: np.mean(
                [abs(r["predictions"].get(name, r["predictions"]["carry"]) - r["actual"]) for r in past]
            )) if len(past) >= 6 and available else "carry"
            records.append({"target_date": actual_date, "predictions": candidates,
                            "actual": float(obs.iloc[j]), "selected": selected})
        usable = [r for r in records if r["selected"] != "carry"]
        mae = np.mean([abs(r["predictions"][r["selected"]] - r["actual"]) for r in usable]) if usable else np.nan
        baseline = np.mean([abs(r["predictions"]["carry"] - r["actual"]) for r in usable]) if usable else np.nan
        passed = len(usable) >= 12 and baseline > 0 and mae <= .95 * baseline
        evaluations.append({"horizon_days": horizon, "n": len(usable), "mae": clean(mae),
                            "carry_mae": clean(baseline), "passed": bool(passed),
                            "target_tolerance_days": 7, "target": "observed source price"})
        if horizon == 90:
            if usable:
                final_method = min(("trend", "seasonal") if seasonal(obs, asof)["status"] == "supported" else ("trend",),
                                   key=lambda name: np.mean([abs(r["predictions"].get(name, r["predictions"]["carry"]) - r["actual"]) for r in usable]))
            final_errors = [abs(np.log(r["predictions"][r["selected"]] / r["actual"])) for r in usable]
            result["status"] = "validated_forecast" if passed else "scenario"
    season = seasonal(obs, asof)
    last = float(obs.iloc[-1])
    scenario_changes = np.abs(np.diff(np.log(obs.values)))
    radius = max(.08, float(np.quantile(final_errors, .8))) if result["status"] == "validated_forecast" else max(.15, float(np.quantile(scenario_changes, .8)))
    # Forecast clock starts at the assessment date, not the last source date.
    for days in (0, 30, 60, 90):
        point = _forecast(obs, days, final_method, season)
        width = radius * np.sqrt(days / 90) if days else 0.
        result["points"].append({"date": iso(asof + pd.Timedelta(days=days)), "days": days,
                                 "value": point, "low": point * np.exp(-width), "high": point * np.exp(width)})
    change = result["points"][-1]["value"] / last - 1
    result.update({"label": "Likely higher" if change > .05 else "Likely lower" if change < -.05 else "Broadly stable",
                   "reason": "Illustrative scenario — not a validated forecast" if result["status"] == "scenario" else "Passed rolling observed-target evaluation against carry baseline.",
                   "confidence": "low" if result["status"] == "scenario" else "medium",
                   "method": final_method, "change_pct": 100 * change, "validation": evaluations,
                   "interval_kind": "historical forecast residual band" if result["status"] == "validated_forecast" else "illustrative historical-volatility range"})
    return clean(result)


def _candidates(target: pd.Series, deal: pd.Series, driver: pd.Series, lag: int, when, gpu=False) -> dict:
    out = {}
    past = target.loc[target.index <= when]
    if not past.empty and (when - past.index[-1]).days <= (30 if gpu else 45):
        out["deal" if gpu else "retail"] = float(past.iloc[-1])
    deals = deal.loc[deal.index <= when]
    if not gpu and not deals.empty and (when - deals.index[-1]).days <= 60:
        ratios = []
        for date, value in past.tail(18).items():
            known = deal.loc[deal.index <= date]
            if not known.empty and (date - known.index[-1]).days <= 60:
                ratios.append(np.log(value / known.iloc[-1]))
        if len(ratios) >= 6:
            out["deal"] = float(deals.iloc[-1] * np.exp(np.median(ratios)))
    # Fit only target dates strictly preceding this estimate's cutoff.
    if not gpu and not driver.empty:
        quote_date = when - pd.Timedelta(days=lag)
        quotes = driver.loc[driver.index <= quote_date]
        x, y = [], []
        for date, value in past.tail(24).items():
            known = driver.loc[driver.index <= date - pd.Timedelta(days=lag)]
            if not known.empty and (date - known.index[-1]).days <= 90:
                x.append(np.log(known.iloc[-1])); y.append(np.log(value))
        if len(x) >= 12 and np.ptp(x) > 1e-6 and not quotes.empty and (when - quotes.index[-1]).days <= 90:
            b, a = np.polyfit(x, y, 1)
            out["driver"] = float(np.exp(a + b * np.log(quotes.iloc[-1])))
    return {k: v for k, v in out.items() if np.isfinite(v) and v > 0}


def benchmark(target, deal, driver, lag, asof, gpu=False) -> dict:
    target = target.loc[target.index <= asof]
    deal = deal.loc[deal.index <= asof]
    driver = driver.loc[driver.index <= asof]
    completed = []
    for date, actual in target.items():
        cutoff = date - pd.Timedelta(nanoseconds=1)
        candidates = _candidates(target, deal, driver, lag, cutoff, gpu)
        if not candidates:
            continue
        weights = {name: 1 / max(.04, np.mean([r["errors"].get(name, .2) for r in completed[-24:]]))
                   if completed else 1 for name in candidates}
        estimate = sum(v * weights[k] for k, v in candidates.items()) / sum(weights.values())
        completed.append({"error": abs(np.log(estimate / actual)), "abs_error": abs(estimate-actual),
                          "errors": {k: abs(np.log(v / actual)) for k, v in candidates.items()}})
    candidates = _candidates(target, deal, driver, lag, asof, gpu)
    dates = {}
    for name in candidates:
        s = target if name == "retail" or gpu else deal if name == "deal" else driver.loc[driver.index <= asof-pd.Timedelta(days=lag)]
        if not s.empty:
            dates[name] = {"date": iso(s.index[-1]), "max_age_days": 30 if gpu else 45 if name == "retail" else 60 if name == "deal" else 90}
    weights = {k: 1 / max(.04, np.mean([r["errors"].get(k, .2) for r in completed[-24:]])) if completed else 1 for k in candidates}
    estimate = sum(v * weights[k] for k, v in candidates.items()) / sum(weights.values()) if candidates else None
    radius = float(np.quantile([r["error"] for r in completed[-24:]], .8)) if len(completed) >= 12 else None
    return clean({"value": estimate, "low": estimate*np.exp(-radius) if estimate and radius is not None else None,
                  "high": estimate*np.exp(radius) if estimate and radius is not None else None,
                  "confidence": "low" if gpu or len(candidates) < 2 else "medium",
                  "candidates": candidates, "candidate_dates": dates, "source_weights": weights,
                  "validation": {"n": len(completed), "mae": np.mean([r["abs_error"] for r in completed]) if completed else None,
                                 "method": "Past-only rolling source weights; next observed target",
                                 "interval_kind": "Empirical absolute log error (80th percentile), not a guaranteed interval"}})


def history(obs):
    if obs.empty:
        return []
    f = obs.reindex(pd.date_range(obs.index.min(), obs.index.max())).to_frame("observed")
    f["filled"] = f.observed.interpolate(method="time", limit_area="inside")
    f["ema"] = f.filled.ewm(span=30, adjust=False).mean()
    previous = pd.Series(pd.NaT, index=f.index, dtype="datetime64[ns]")
    previous.loc[obs.index] = obs.index
    following = previous.bfill()
    previous = previous.ffill()
    return [{"date": iso(date), "observed": clean(row.observed), "value": float(row.filled),
             "ema": float(row.ema), "filled": bool(pd.isna(row.observed)),
             "long_gap": bool(pd.isna(row.observed) and (following.loc[date]-previous.loc[date]).days > 30)}
            for date, row in f.iterrows()]


def build_snapshot(data_dir: Path, asof=None) -> dict:
    asof = pd.Timestamp(asof).normalize() if asof is not None else today()
    prices = pd.read_parquet(data_dir / "price_daily.parquet")
    prices["date"] = pd.to_datetime(prices.date).dt.normalize()
    assets = {}
    audit = []
    deals = {}
    frames = []
    deals_path = data_dir / "deals_bapcs.csv"
    if deals_path.exists():
        frames.append(pd.read_csv(deals_path))
    text_path = data_dir / "text_event.parquet"
    if text_path.exists():
        text = pd.read_parquet(text_path, columns=["ts", "source", "title", "url"])
        frames.append(text.loc[text.source.eq("reddit:buildapcsales") & text.title.str.match(r"^\s*\[GPU\]", case=False, na=False)])
    elif (data_dir / "gpu_cpu_1d/parsed_deals.csv").exists():
        frames.append(pd.read_csv(data_dir / "gpu_cpu_1d/parsed_deals.csv"))
    accepted = set()
    for df in frames:
        for r in df.to_dict("records"):
            date = pd.Timestamp(r.get("ts") or r.get("date"))
            if date.tzinfo:
                date = date.tz_convert("UTC").tz_localize(None)
            date = date.normalize()
            title = str(r["title"])
            product = parse_title(title)
            reason = "future_date" if date > asof else "condition_or_bundle" if BAD_CONDITION.search(title) else "ambiguous_product" if not product or ambiguous_title(title) else None
            amounts = re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", title)
            if len(set(amounts)) > 1:
                reason = reason or "ambiguous_price_or_coupon"
            price = _price(title)
            if product and product["category"] == "GPU" and product["model"] not in GPU_MODELS:
                reason = reason or "unknown_or_ambiguous_gpu_model"
            if not price or not np.isfinite(price) or price <= 0:
                reason = reason or "invalid_price"
            if reason:
                audit.append({"date": iso(date), "url": r.get("url"), "reason": reason})
                continue
            key = product["model"] if product["category"] == "GPU" else ("RAM_" + product["generation"] if product["category"] == "RAM" else "SSD_" + product["interface"])
            value = price if product["category"] == "GPU" else price / product["capacity_gb"]
            bounds = (50, 4000) if product["category"] == "GPU" else (.3, 40) if product["category"] == "RAM" else (.015, 1)
            if not bounds[0] <= value <= bounds[1]:
                audit.append({"date": iso(date), "url": r.get("url"), "reason": "price_out_of_range"})
                continue
            identity = (key, str(r.get("url")), iso(date))
            if identity in accepted:
                continue
            accepted.add(identity)
            deals.setdefault(key, []).append((date, value))
    for key, rows in deals.items():
        deals[key] = pd.Series([r[1] for r in rows], index=[r[0] for r in rows]).groupby(level=0).median().sort_index()
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    for label, (category, key, retail, chip, lag) in SEGMENTS.items():
        assets[key] = {"category": category, "label": label, "target": source_series(prices, retail, asof),
                       "deal": deals.get(key, empty), "driver": source_series(prices, chip, asof), "lag": lag}
    for key, obs in deals.items():
        if key.startswith(("RX ", "RTX ", "GTX ")):
            assets[key] = {"category": "GPU", "label": key, "target": obs, "deal": obs, "driver": empty, "lag": 0}
    output = {}
    for key, asset in assets.items():
        obs = asset["target"]
        if obs.empty:
            continue
        output[key] = {"category": asset["category"], "label": asset["label"], "unit": UNITS[asset["category"]],
                       "last_observed_date": iso(obs.index[-1]), "observed_days": len(obs),
                       "channels": {"deal": dated(asset["deal"]), "retail": dated(obs) if asset["category"] != "GPU" else []},
                       "history": history(obs), "reference": benchmark(obs, asset["deal"], asset["driver"], asset["lag"], asof, asset["category"] == "GPU"),
                       "seasonality": seasonal(obs, asof), "outlook": outlook(obs, asof),
                       "sources": ["reddit:buildapcsales"] if asset["category"] == "GPU" else ["memradar", "reddit:buildapcsales", "trendforce_spot"]}
    fx = source_series(prices, "FX-USDTHB", asof)
    result = clean({"schema_version": SCHEMA_VERSION, "asof": iso(asof), "assets": output,
                    "gpu_context": {"label": "GPU category index (not a model price)", "unit": "index",
                                    "points": dated(source_series(prices, "GPX-GPU-RETAIL-INDEX", asof))},
                    "fx": {"rate": float(fx.iloc[-1]) if len(fx) else None, "date": iso(fx.index[-1]) if len(fx) else None},
                    "audit": {"rejected_count": len(audit), "rejected": audit},
                    "limitations": ["US advertised prices, not completed transactions or Thai matched-product prices.",
                                    "Historical retail catalog contains products still sold in 2026 (selection bias).",
                                    "Observation dates do not establish historical publication availability.",
                                    "Filled history uses future endpoints; it is excluded from model evaluation."]})
    result["dataset_version"] = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
    return result


def validate_snapshot(data):
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or not data.get("dataset_version") or not isinstance(data.get("assets"), dict):
        raise ValueError("Invalid assessment snapshot")
    cutoff = pd.Timestamp(data["asof"])
    for asset in data["assets"].values():
        if pd.Timestamp(asset["last_observed_date"]) > cutoff:
            raise ValueError("Future observation in snapshot")
        for r in asset["history"]:
            if pd.Timestamp(r["date"]) > cutoff or r["value"] <= 0 or not np.isfinite(r["value"]):
                raise ValueError("Invalid history point")
    json.dumps(data, allow_nan=False)


def write_snapshot(data, path: Path):
    validate_snapshot(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".assessment-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, allow_nan=False, separators=(",", ":"))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class SnapshotStore:
    def __init__(self, path):
        self.path = Path(path)
        self.data = None
        self.signature = None
        self.error = None
        self.lock = threading.Lock()

    def load(self):
        with self.lock:
            try:
                stat = self.path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if signature != self.signature:
                    data = json.loads(self.path.read_text())
                    validate_snapshot(data)
                    self.data, self.signature, self.error = data, signature, None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.error = "Latest snapshot unavailable; retaining last verified dataset." if self.data else "Assessment dataset unavailable."
            return self.data


def assess(data, title, price=None, *, when=None, product=None, model=None, capacity_gb=None,
           confirmed=False, ambiguous=False, title_source=None, price_source=None,
           variant_title=None, variant_label=None):
    when = pd.Timestamp(when).normalize() if when is not None else today()
    parsed = parse_title(title)
    if not confirmed and variant_title and variant_label:
        variant = parse_title(variant_title)
        if not variant or not parsed or variant["category"] != parsed["category"]:
            raise ValueError("The selected option does not match the listing category.")
        parsed = variant
    if confirmed and product:
        if product == "GPU":
            candidate = parse_title("Graphics card " + (model or ""))
            if not candidate or candidate["category"] != "GPU":
                raise ValueError("Select a valid GPU model.")
            parsed = candidate
        elif product in SEGMENTS and capacity_gb and 0 < capacity_gb <= 16000:
            if (product.startswith("DDR") and capacity_gb not in VALID_GB) or (product in {"NVME", "SATA"} and capacity_gb < 120):
                raise ValueError("Choose a valid capacity for this product type.")
            parsed = {"category": SEGMENTS[product][0], "generation" if product.startswith("DDR") else "interface": product, "capacity_gb": capacity_gb}
        else:
            raise ValueError("Confirm a supported product and capacity.")
    if not parsed:
        return {"covered": False, "schema_version": 2, "reason": "Product type or interface could not be identified."}
    category = parsed["category"]
    selection_note = None
    identity_title = variant_title if variant_title and variant_label and not confirmed else title
    if variant_title and variant_label and not confirmed:
        selection_note = "Selected shop option: " + variant_label + ". Price read from the product price area."
    if not confirmed and not variant_title and category == "SSD":
        caps = {float(n)*(1000 if u.upper()=="TB" else 1) for n,u in re.findall(r"(\d+(?:\.\d+)?)\s*(TB|GB)\b",title,re.I)}
        ambiguous = ambiguous or len(caps)>1
    key = parsed["model"] if category == "GPU" else "RAM_" + parsed["generation"] if category == "RAM" else "SSD_" + parsed["interface"]
    needs_confirmation = bool(not confirmed and (ambiguous or ambiguous_title(identity_title) or BAD_CONDITION.search(title)))
    factor = 1 if category == "GPU" else parsed["capacity_gb"]
    result = {"covered": True, "schema_version": 2, "dataset_version": data["dataset_version"],
              "dataset_asof": data["asof"], "assessment_date": iso(when), "product": parsed,
              "product_match": {"needs_confirmation": needs_confirmation, "confirmed": confirmed,
                                "selection_note": selection_note,
                                "title_source": title_source or ("manual" if confirmed else "listing_title"),
                                "price_source": price_source or ("manual" if confirmed else "listing")},
              "price_assessment": {"status": "insufficient", "label": "No reference range", "listing_thb": price,
                                   "reference_thb": None, "low_thb": None, "high_thb": None, "diff_pct": None,
                                   "basis": REFERENCE_LABEL, "scope": "Model reference" if category == "GPU" else "Category reference"},
              "seasonality": {"status": "historical_only", "label": "No clear seasonal pattern", "profile": [], "reason": "No matched history.", "years": 0},
              "outlook": {"status": "unavailable", "label": "Uncertain", "reason": "Not enough recent evidence", "points": [], "validation": []},
              "history": [], "channels": {}, "category_context": None,
              "evidence": {"limitations": data["limitations"], "confidence": "none"}}
    asset = data["assets"].get(key)
    query = {"title": title, "price": price} if price else {"title": title}
    if variant_title and variant_label and not confirmed:
        query.update({"variant_title":variant_title,"variant_label":variant_label,"price_source":price_source})
    if confirmed:
        query.update({"product": product, "model": model, "capacity_gb": capacity_gb, "confirmed": "true"})
    elif ambiguous:
        query["ambiguous"] = "true"
    result["dashboard"] = "/dashboard?" + urlencode({k:v for k,v in query.items() if v is not None})
    if not asset:
        result["price_assessment"]["label"] = "No matching reference"
        result["price_assessment"]["scope"] = "Category reference"
        result["price_assessment"]["reason"] = "No model-level benchmark. Category context only; no price verdict."
        result["category_context"] = data.get("gpu_context") if category == "GPU" else None
        return result
    fx = data["fx"]
    fx_age = (when-pd.Timestamp(fx["date"])).days if fx["date"] else None
    fx_ok = bool(fx["rate"] and fx_age is not None and 0 <= fx_age <= 7)
    ref = asset["reference"]
    age = (when-pd.Timestamp(asset["last_observed_date"])).days
    fresh = 0 <= age <= (30 if category == "GPU" else 45)
    candidates_fresh = all(0 <= (when-pd.Timestamp(v["date"])).days <= v["max_age_days"] for v in ref.get("candidate_dates", {}).values())
    fresh = fresh and candidates_fresh
    convert = lambda value: round(value * fx["rate"] * factor, 2) if value is not None and fx["rate"] else None
    season = dict(asset["seasonality"])
    profile = {r["month"]:r["index"] for r in season["profile"] if r["index"] is not None}
    if season["status"] == "supported" and when.month in profile:
        ordered = sorted(profile, key=profile.get)
        season["label"] = "Historically favorable" if when.month in ordered[:4] else "Historically less favorable" if when.month in ordered[-4:] else "Typical period"
    season["current_month"] = when.month
    rx_candidates = {"RX 6700 XT", "RX 6800 XT", "RX 6900 XT", "RX 6750 XT", "RX 6800", "RX 6650 XT"}
    season["group"] = "Candidate seasonal group" if key in rx_candidates else "Model-specific pattern" if category == "GPU" else "Category pattern"
    result["seasonality"] = season
    result["history"] = [{"date":r["date"], "observed_thb":convert(r["observed"]), "value_thb":convert(r["value"]),
                          "ema_thb":convert(r["ema"]), "filled":r["filled"], "long_gap":r["long_gap"]} for r in asset["history"]]
    result["channels"] = {name: [{"date":r["date"],"value_thb":convert(r["value"])} for r in rows] for name,rows in asset["channels"].items()}
    pa = result["price_assessment"]
    pa.update({"reference_thb":convert(ref["value"]), "low_thb":convert(ref["low"]), "high_thb":convert(ref["high"]),
               "reason":"Compared with a US advertised-price reference; Thai-market markup is uncalibrated."})
    # Price is a comparison with the displayed band, not a combined model score.
    # Timing, outlook, freshness and identity warnings remain independently visible.
    band_ok = pa["low_thb"] is not None and pa["high_thb"] is not None and 0 < pa["low_thb"] <= pa["high_thb"]
    pa["comparison_only"] = True
    pa["provisional"] = bool(needs_confirmation or not fresh or not fx_ok)
    pa["warnings"] = (["Product option is unverified; comparison assumes the displayed product and capacity."] if needs_confirmation else []) + (["Reference or FX is older than the freshness limit; this is a historical comparison, not a current market valuation."] if not fresh or not fx_ok else [])
    if price and band_ok:
        status = "below" if price < pa["low_thb"] else "above" if price > pa["high_thb"] else "within"
        pa.update({"status":status, "label":{"below":"Below reference","above":"Above reference","within":"Within reference range"}[status],
                   "diff_pct":round(100*(price/pa["reference_thb"]-1),1) if pa["reference_thb"] else None})
        if pa["provisional"]:
            pa["label"] += " · Provisional"
        pa["reason"] = "Compared only with the displayed US reference range converted to THB; not a calibrated Thai market valuation. " + " ".join(pa["warnings"])
    else:
        pa["label"] = "Enter a listing price" if not price else "No reference range"
        pa["reason"] = "Enter a single listing price to compare with the displayed reference range." if not price else "No usable reference range is available for this product."
    result["outlook"] = dict(asset["outlook"])
    result["outlook"]["asof"] = data["asof"]
    result["outlook"]["price_basis"] = "Outlook starts from the latest observed benchmark, which may differ from the blended reference midpoint."
    if not fresh or not fx_ok or needs_confirmation or (when-pd.Timestamp(data["asof"])).days > 7:
        result["outlook"].update({"status":"unavailable","label":"Uncertain","reason":"Not enough recent evidence or product confirmation.","points":[],"interval_kind":None})
    else:
        result["outlook"]["points"] = [dict(r, value_thb=convert(r["value"]),low_thb=convert(r["low"]),high_thb=convert(r["high"])) for r in asset["outlook"]["points"]]
    result["outlook"]["fx_assumption"] = "FX held constant at assessment rate; no currency forecast."
    result["evidence"].update({"last_observed_date":asset["last_observed_date"],"age_days":age,"stale":not fresh or not fx_ok,
                              "observed_days":asset["observed_days"],"sources":asset["sources"],
                              "confidence":ref["confidence"] if fresh and fx_ok else "none",
                              "fx_rate":fx["rate"],"fx_date":fx["date"],"fx_age_days":fx_age,
                              "reference_validation":ref["validation"],
                              "filled_share_pct":round(100*sum(r["filled"] for r in asset["history"])/len(asset["history"]),1),
                              "historical_fx":"All history shown at the snapshot FX rate for comparability, not historical Thai quotes."})
    return clean(result)
