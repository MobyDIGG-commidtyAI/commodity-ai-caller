import pandas as pd

from cai import audit


def weekly(sku, source, start, end, drop=()):
    d = pd.date_range(start, end, freq="7D")
    d = d[~d.isin(pd.to_datetime(list(drop)))]
    return pd.DataFrame({"sku": sku, "source": source, "date": d, "price": 1.0})


def test_price_coverage_finds_holes_and_staleness():
    price = pd.concat([
        weekly("TF-X", "trendforce_spot", "2024-01-03", "2024-06-26", drop=["2024-03-06", "2024-03-13", "2024-03-20"]),
        weekly("DEAL-RAM-DDR5-USD_PER_GB", "reddit_bapcs", "2024-01-07", "2024-03-31"),
    ])
    cov = audit.price_coverage(price, pd.Timestamp("2024-06-30")).set_index("series")
    tf = cov.loc["TF-X|trendforce_spot"]
    assert (tf.n_holes, tf.max_gap_days, tf.status) == (1, 28, "ok")
    assert cov.loc["DEAL-RAM-DDR5-USD_PER_GB|reddit_bapcs", "status"] == "known"   # explained staleness


def test_daily_series_weekend_is_not_a_hole():
    d = pd.bdate_range("2024-01-01", "2024-02-02")              # Mon-Fri only
    price = pd.DataFrame({"sku": "EQ-X", "source": "yfinance", "date": d, "price": 1.0})
    row = audit.price_coverage(price, pd.Timestamp("2024-02-05")).iloc[0]   # Monday after last Friday
    assert (row.n_holes, row.status) == (0, "ok")


def test_weekly_events_split_posts_and_comments():
    text = pd.DataFrame({"source": ["reddit:hardware", "reddit_c:hardware", "reddit_c:hardware", "hn"],
                         "category": ["RAM"] * 4, "is_canonical": [1, 1, 0, 1],
                         "ts": pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-07", "2025-01-08"])})
    we = audit.weekly_events(text)
    assert we[("RAM", "posts")].iloc[0] == 2 and we[("RAM", "comments")].iloc[0] == 1
