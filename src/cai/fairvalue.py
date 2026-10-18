"""Baseline fair value for RAM retail prices: what a GB of DDR4/DDR5 *should* cost given the chip price.

Method (baseline - meant to be replaced by the team's model, keep the interface):
  log(retail USD/GB) = a + b * log(DDR4 chip spot [t - L weeks])
  - retail   : MemRadar monthly median USD/GB (Amazon), dated at month end
  - chip     : TrendForce DDR4 1Gx8 spot, weekly; the only chip series that spans the fit window
  - L        : measured lead (scripts/lead_lag.py): 2 weeks for DDR5, 4 for DDR4
  - fit window = months where both exist (2023-07 onward)
Because the chip price leads, the expected line runs L weeks past the last chip quote: that tail is
the "where retail is heading" part of the demo.

Thai price: expected THB/GB = expected USD/GB x USD/THB x markup. The markup is unknown (no dated Thai
history), so it is a parameter that defaults to 1.0 and must be shown as uncalibrated.
Verdict band = +/- one residual standard deviation of the fit (in log space).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CHIP = "TF-DDR4-1GX8|trendforce_spot"
RETAIL = {"DDR5": "MR-DDR5-USD_PER_GB|memradar", "DDR4": "MR-DDR4-USD_PER_GB|memradar"}
LEAD_WEEKS = {"DDR5": 2, "DDR4": 4}
FX = "FX-USDTHB|yfinance"
GPU_INDEX = "GPX-GPU-RETAIL-INDEX|gpuprix"


def series(price: pd.DataFrame, key: str) -> pd.Series:
    sku, src = key.split("|")
    s = price[(price["sku"] == sku) & (price["source"] == src)]
    return pd.Series(s["price"].values, index=pd.to_datetime(s["date"]), name=key).sort_index()


def _asof(s: pd.Series, dates) -> np.ndarray:
    """Last known value of s at each date (NaN before the series starts)."""
    idx = pd.DatetimeIndex(dates)
    pos = s.index.searchsorted(idx, side="right") - 1
    out = np.where(pos >= 0, s.values[np.clip(pos, 0, None)], np.nan)
    return out.astype(float)


@dataclass
class GenModel:
    generation: str
    a: float
    b: float
    lag_weeks: int
    resid_sd: float      # log space
    r2: float
    n: int

    def predict(self, chip_usd: np.ndarray) -> np.ndarray:
        return np.exp(self.a + self.b * np.log(chip_usd))


class FairValue:
    def __init__(self, price: pd.DataFrame):
        self.price = price
        self.chip = series(price, CHIP)
        self.fx = series(price, FX)
        self.retail = {g: series(price, k) for g, k in RETAIL.items()}
        self.models = {g: self._fit(g) for g in RETAIL}

    def _fit(self, gen: str) -> GenModel:
        r, lag = self.retail[gen], LEAD_WEEKS[gen]
        x = _asof(self.chip, r.index - pd.Timedelta(weeks=lag))
        ok = ~np.isnan(x)
        lx, ly = np.log(x[ok]), np.log(r.values[ok])
        b, a = np.polyfit(lx, ly, 1)
        res = ly - (a + b * lx)
        return GenModel(gen, float(a), float(b), lag, float(res.std(ddof=2)), float(1 - res.var() / ly.var()), int(ok.sum()))

    # --- curves for the dashboard -------------------------------------------------------------
    def last_expected_date(self, gen: str) -> pd.Timestamp:
        return self.chip.index[-1] + pd.Timedelta(weeks=self.models[gen].lag_weeks)

    def expected_usd_per_gb(self, gen: str, when) -> float:
        m = self.models[gen]
        when = min(pd.Timestamp(when), self.last_expected_date(gen))
        return float(m.predict(_asof(self.chip, [when - pd.Timedelta(weeks=m.lag_weeks)]))[0])

    def usdthb(self, when=None) -> float:
        return float(self.fx.iloc[-1] if when is None else _asof(self.fx, [pd.Timestamp(when)])[0])

    def curve(self, gen: str, start: str = "2024-01-01", markup: float = 1.0) -> dict:
        """Weekly expected line + monthly actual points, both in THB/GB (USD x FX x markup for expected,
        USD x FX for actual)."""
        m = self.models[gen]
        weeks = pd.date_range(pd.Timestamp(start), self.last_expected_date(gen), freq="W-SUN")
        weeks = weeks.append(pd.DatetimeIndex([self.last_expected_date(gen)])).unique()
        chip = _asof(self.chip, weeks - pd.Timedelta(weeks=m.lag_weeks))
        fx = _asof(self.fx, weeks)
        fx = np.where(np.isnan(fx), self.usdthb(), fx)
        exp = m.predict(chip) * fx * markup
        act = self.retail[gen][self.retail[gen].index >= pd.Timestamp(start)]
        act_fx = _asof(self.fx, act.index)
        return {
            "expected": [{"date": d.strftime("%Y-%m-%d"), "thb_per_gb": round(float(v), 2)}
                         for d, v in zip(weeks, exp) if not np.isnan(v)],
            "actual": [{"date": d.strftime("%Y-%m-%d"), "thb_per_gb": round(float(v * f), 2)}
                       for d, v, f in zip(act.index, act.values, act_fx)],
            "band_pct": round(float(np.expm1(m.resid_sd)) * 100, 1),
        }

    # --- verdict for one listing --------------------------------------------------------------
    def verdict(self, gen: str, capacity_gb: int, price_thb: float, today=None, markup: float = 1.0) -> dict:
        m = self.models[gen]
        today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
        fx = self.usdthb()
        exp_now = self.expected_usd_per_gb(gen, today) * fx * markup
        exp_ahead = self.expected_usd_per_gb(gen, self.last_expected_date(gen)) * fx * markup
        per_gb = price_thb / capacity_gb
        diff = per_gb / exp_now - 1
        band = float(np.expm1(m.resid_sd))
        verdict = "under" if diff < -band else "over" if diff > band else "fair"
        # direction the chip price has already set for retail: expected line over its last 8 weeks
        end = self.last_expected_date(gen)
        change = exp_ahead / (self.expected_usd_per_gb(gen, end - pd.Timedelta(weeks=8)) * fx * markup) - 1
        outlook = "rising" if change > 0.03 else "falling" if change < -0.03 else "flat"
        return {
            "category": "RAM", "generation": gen, "capacity_gb": capacity_gb, "price_thb": round(price_thb, 2),
            "thb_per_gb": round(per_gb, 2), "expected_thb_per_gb": round(exp_now, 2),
            "expected_price_thb": round(exp_now * capacity_gb), "diff_pct": round(diff * 100, 1),
            "band_pct": round(band * 100, 1), "verdict": verdict,
            "outlook": {"direction": outlook, "change_pct_8w": round(change * 100, 1),
                        "until": end.strftime("%Y-%m-%d")},
            "basis": {"model": "baseline: log retail USD/GB ~ log DDR4 chip spot lagged", "lag_weeks": m.lag_weeks,
                      "r2": round(m.r2, 3), "n_months": m.n, "usdthb": round(fx, 3), "markup": markup,
                      "markup_calibrated": False, "chip_last_date": self.chip.index[-1].strftime("%Y-%m-%d")},
        }

    # --- GPU: trend only ----------------------------------------------------------------------
    def gpu_trend(self) -> dict:
        s = series(self.price, GPU_INDEX)
        last = s.iloc[-1]
        chg = {f"{k}m": round(float(last / s.iloc[-1 - k] - 1) * 100, 1) for k in (1, 3, 6) if len(s) > k}
        return {"category": "GPU", "index_last": round(float(last), 1), "index_month": s.index[-1].strftime("%Y-%m"),
                "change_pct": chg, "series": [{"date": d.strftime("%Y-%m-%d"), "index": round(float(v), 2)} for d, v in s.items()],
                "verdict": None, "note": "GPU has a market index only (GpuPrix), no per-model fair value yet"}
