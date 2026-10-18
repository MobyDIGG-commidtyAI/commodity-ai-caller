"""Lead/lag report per category -> data/export/lead_lag_<CAT>.csv (chip -> retail, text -> prices)."""
import sys, pathlib
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import leadlag

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "export"
PAIRS = {   # (leader column, kind), (follower column, kind)
    "RAM": [(("TF-DDR4-1GX8|trendforce_spot", "price"), ("DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs", "sparse_price")),
            (("TF-DDR4-1GX8|trendforce_spot", "price"), ("DEAL-RAM-DDR4-USD_PER_GB|reddit_bapcs", "sparse_price")),
            (("TF-DDR4-1GX8|trendforce_spot", "price"), ("MR-DDR5-INDEX|memradar", "price")),
            (("TF-DDR4-1GX8|trendforce_spot", "price"), ("MR-DDR4-INDEX|memradar", "price")),
            (("EQ-MICRON|yfinance", "price"), ("TF-DDR4-1GX8|trendforce_spot", "price")),
            (("txt_kw_dir_mean", "signal"), ("TF-DDR4-1GX8|trendforce_spot", "price")),
            (("txt_kw_dir_mean", "signal"), ("DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs", "sparse_price"))],
    "SSD": [(("TF-NAND-512GB-TLC|trendforce_spot", "price"), ("DEAL-SSD-NVME-USD_PER_TB|reddit_bapcs", "sparse_price")),
            (("TF-NAND-512GB-TLC|trendforce_spot", "price"), ("MR-NVME-USD_PER_GB|memradar", "price")),
            (("EQ-SNDK|yfinance", "price"), ("TF-NAND-512GB-TLC|trendforce_spot", "price")),
            (("txt_kw_dir_mean", "signal"), ("TF-NAND-512GB-TLC|trendforce_spot", "price")),
            (("txt_kw_dir_mean", "signal"), ("DEAL-SSD-NVME-USD_PER_TB|reddit_bapcs", "sparse_price"))],
    "GPU": [(("EQ-NVIDIA|yfinance", "price"), ("GPX-GPU-RETAIL-INDEX|gpuprix", "price")),
            (("txt_kw_dir_mean", "signal"), ("GPX-GPU-RETAIL-INDEX|gpuprix", "price"))],
}
since = sys.argv[1] if len(sys.argv) > 1 else "2023-07-01"
for cat, pairs in PAIRS.items():
    f = pd.read_csv(OUT / f"features_{cat}.csv", parse_dates=["week"]).set_index("week")
    f = f[f.index >= since]
    rows = []
    for (xc, xk), (yc, yk) in pairs:
        if xc not in f or yc not in f:
            continue
        r = leadlag.lead_lag(leadlag.prepare(f[xc], xk), leadlag.prepare(f[yc], yk))
        rows.append({"leader": xc, "follower": yc, **r})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / f"lead_lag_{cat}.csv", index=False)
    print(f"== {cat} (since {since})\n{out.to_string(index=False)}")
