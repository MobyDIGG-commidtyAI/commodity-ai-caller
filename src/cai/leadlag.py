"""Lead/lag between weekly series: does X move before Y, and by how many weeks?

Method (deliberately simple, explainable in a pitch):
  - each series -> weekly log change over `horizon` weeks (sparse deal series are first smoothed
    with a 5-week rolling median); a text signal is used as a level (it is already a rate)
  - corr(X shifted by k weeks, Y change) for k = 0..max_lag; best k = highest correlation
  - pairs with fewer than `min_overlap` overlapping weeks are flagged, not reported
Overlapping 4-week changes are autocorrelated, so treat the correlation as descriptive, not a p-value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def prepare(s: pd.Series, kind: str, horizon: int = 4) -> pd.Series:
    if kind == "signal":
        return s.rolling(horizon, min_periods=2).mean()
    if kind == "sparse_price":
        s = s.rolling(5, min_periods=2).median()
    return np.log(s).diff(horizon)


def lead_lag(x: pd.Series, y: pd.Series, max_lag: int = 26, min_overlap: int = 26) -> dict:
    """x leads y by k weeks if corr(x.shift(k), y) is highest at k."""
    rows = []
    for k in range(max_lag + 1):
        pair = pd.concat([x.shift(k), y], axis=1).dropna()
        if len(pair) >= 3 and pair.iloc[:, 0].std() > 0 and pair.iloc[:, 1].std() > 0:
            rows.append((k, float(pair.iloc[:, 0].corr(pair.iloc[:, 1])), len(pair)))
    if not rows:
        return {"best_lag_weeks": None, "corr": None, "corr_lag0": None, "n_overlap": 0, "flag": "insufficient_overlap"}
    k, c, n = max(rows, key=lambda r: r[1])
    lag0 = next((r[1] for r in rows if r[0] == 0), None)
    flag = "insufficient_overlap" if n < min_overlap else ""
    return {"best_lag_weeks": None if flag else k, "corr": None if flag else round(c, 3),
            "corr_lag0": None if lag0 is None else round(lag0, 3), "n_overlap": n, "flag": flag}
