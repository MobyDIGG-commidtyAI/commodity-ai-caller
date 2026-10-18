import numpy as np
import pandas as pd

from cai.fairvalue import FairValue


def synthetic_price() -> pd.DataFrame:
    weeks = pd.date_range("2023-07-05", "2026-10-07", freq="7D")
    chip = np.exp(np.linspace(np.log(1.5), np.log(40), len(weeks)))
    rows = [{"sku": "TF-DDR4-1GX8", "source": "trendforce_spot", "date": d, "price": c} for d, c in zip(weeks, chip)]
    months = pd.date_range("2023-07-31", "2026-09-30", freq="ME")
    for gen, lag, a, b in (("DDR5", 2, 0.9, 0.5), ("DDR4", 4, 0.5, 0.4)):
        c = pd.Series(chip, index=weeks).reindex(pd.Series(chip, index=weeks).index.union(months - pd.Timedelta(weeks=lag))).ffill()
        x = c.reindex(months - pd.Timedelta(weeks=lag)).values
        noise = np.where(np.arange(len(months)) % 2, 0.05, -0.05)
        rows += [{"sku": f"MR-{gen}-USD_PER_GB", "source": "memradar", "date": d, "price": float(np.exp(a + b * np.log(v) + e))}
                 for d, v, e in zip(months, x, noise) if not np.isnan(v)]
    rows += [{"sku": "FX-USDTHB", "source": "yfinance", "date": d, "price": 34.0} for d in pd.date_range("2023-01-01", "2026-10-14")]
    rows += [{"sku": "GPX-GPU-RETAIL-INDEX", "source": "gpuprix", "date": d, "price": 100 + i} for i, d in
             enumerate(pd.date_range("2024-05-31", "2026-09-30", freq="ME"))]
    return pd.DataFrame(rows)


def test_fit_recovers_slope_and_band():
    fv = FairValue(synthetic_price())
    m = fv.models["DDR5"]
    assert abs(m.b - 0.5) < 0.02 and m.lag_weeks == 2 and m.r2 > 0.98
    assert 4 < fv.curve("DDR5")["band_pct"] < 6


def test_expected_line_runs_past_last_chip_quote():
    fv = FairValue(synthetic_price())
    c = fv.curve("DDR5")
    assert c["expected"][-1]["date"] == "2026-10-21"          # last chip 2026-10-07 + 2 weeks
    assert c["actual"][-1]["date"] == "2026-09-30"


def test_verdict_bands():
    fv = FairValue(synthetic_price())
    fair = fv.verdict("DDR5", 32, 1.0, today="2026-10-16")
    exp_total = fair["expected_price_thb"]
    assert fv.verdict("DDR5", 32, exp_total, today="2026-10-16")["verdict"] == "fair"
    assert fv.verdict("DDR5", 32, exp_total * 0.7, today="2026-10-16")["verdict"] == "under"
    over = fv.verdict("DDR5", 32, exp_total * 1.3, today="2026-10-16")
    assert over["verdict"] == "over" and over["outlook"]["direction"] == "rising"
    assert over["basis"]["markup_calibrated"] is False


def test_markup_scales_expected_price():
    fv = FairValue(synthetic_price())
    a = fv.verdict("DDR4", 16, 1000, today="2026-10-16")["expected_thb_per_gb"]
    b = fv.verdict("DDR4", 16, 1000, today="2026-10-16", markup=1.2)["expected_thb_per_gb"]
    assert abs(b / a - 1.2) < 1e-3


def test_gpu_is_trend_only():
    t = FairValue(synthetic_price()).gpu_trend()
    assert t["verdict"] is None and t["change_pct"]["1m"] > 0
