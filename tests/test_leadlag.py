import numpy as np
import pandas as pd

from cai import leadlag


def test_recovers_known_lag():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-07", periods=150, freq="W-SUN")
    x = pd.Series(rng.normal(size=150), index=idx).rolling(3, min_periods=1).mean()
    y = x.shift(8) + rng.normal(scale=0.05, size=150)
    r = leadlag.lead_lag(x, y, max_lag=20)
    assert r["best_lag_weeks"] == 8 and r["corr"] > 0.9 and not r["flag"]


def test_flags_short_overlap():
    idx = pd.date_range("2024-01-07", periods=20, freq="W-SUN")
    s = pd.Series(np.arange(20, dtype=float), index=idx)
    r = leadlag.lead_lag(s, s, max_lag=5, min_overlap=26)
    assert r["flag"] == "insufficient_overlap" and r["best_lag_weeks"] is None
