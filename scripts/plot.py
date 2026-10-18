"""Charts from data/export/features_<CAT>.csv -> data/export/features_<CAT>.png
Top panel: chip spot vs retail deal price, each indexed to 100 at the first week both exist (log scale,
one axis - different units, so we compare *moves*, not levels). Bottom panel: keyword-direction text
signal (4-week mean) so the lead/lag is visible on the same time axis.
Deal series are drawn as a 5-week rolling median (few deals per week); gaps > 4 weeks are left blank.
usage: python scripts/plot.py [RAM,SSD]"""
import sys, pathlib
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "export"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]          # categorical slots 1-3, fixed order
PANELS = {
    "RAM": [("TF-DDR4-1GX8|trendforce_spot", "DDR4 1Gx8 chip spot (TrendForce)"),
            ("DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs", "DDR5 retail deals, $/GB (r/buildapcsales)"),
            ("DEAL-RAM-DDR4-USD_PER_GB|reddit_bapcs", "DDR4 retail deals, $/GB (r/buildapcsales)")],
    "SSD": [("TF-NAND-512GB-TLC|trendforce_spot", "512Gb TLC wafer spot (TrendForce)"),
            ("DEAL-SSD-NVME-USD_PER_TB|reddit_bapcs", "NVMe retail deals, $/TB (r/buildapcsales)"),
            ("DEAL-SSD-SATA-USD_PER_TB|reddit_bapcs", "SATA retail deals, $/TB (r/buildapcsales)")],
}


def plot(cat: str, since: str = "2024-01-01") -> pathlib.Path:
    f = pd.read_csv(OUT / f"features_{cat}.csv", parse_dates=["week"]).set_index("week")
    f = f[f.index >= since]
    series = [(c, lab) for c, lab in PANELS[cat] if c in f.columns and f[c].notna().any()]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]},
                                  facecolor=SURFACE)
    base = f[[c for c, _ in series]].dropna().index.min()
    for i, (c, lab) in enumerate(series):
        s = f[c]
        if c.endswith("|reddit_bapcs"):           # few deals per week -> 5-week rolling median
            s = s.rolling(5, min_periods=2).median()
        s = s[s.index >= base]                    # NaN weeks stay NaN so gaps show as breaks
        idx = 100 * s / s.dropna().iloc[0]
        ax.plot(idx.index, idx.values, color=SERIES[i], lw=2, label=lab)
        last = idx.dropna()
        ax.annotate(f"{last.iloc[-1]:.0f}", (last.index[-1], last.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", color=INK, fontsize=9)
    ax.set_yscale("log")
    ax.set_ylabel(f"index, {base:%d %b %Y} = 100 (log)")
    ax.set_title(f"{cat}: chip spot vs retail deal price, and the text signal", loc="left", fontsize=13, color=INK)
    ax.legend(frameon=False, loc="upper left", fontsize=9, labelcolor=INK)

    sig = f["txt_kw_dir_mean"].rolling(4, min_periods=2).mean() if "txt_kw_dir_mean" in f else pd.Series(dtype=float)
    ax2.axhline(0, color=INK2, lw=1)
    ax2.plot(sig.index, sig.values, color=INK2, lw=2)
    ax2.set_ylabel("keyword\ndirection (4wk)")
    for a in (ax, ax2):
        a.set_facecolor(SURFACE)
        a.grid(True, color=GRID, lw=0.8)
        for sp in ("top", "right"):
            a.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            a.spines[sp].set_color(GRID)
    fig.tight_layout()
    p = OUT / f"features_{cat}.png"
    fig.savefig(p, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return p


if __name__ == "__main__":
    for cat in (sys.argv[1] if len(sys.argv) > 1 else "RAM,SSD").split(","):
        print(plot(cat))
