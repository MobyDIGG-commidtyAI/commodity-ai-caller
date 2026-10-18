"""Merge everything -> data/export/features_<CAT>.csv (weekly, lagged). This is what พี่ชิน models on."""
import sys, pathlib, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, features
out = pathlib.Path(__file__).resolve().parents[1] / "data" / "export"
con = schema.connect()
price = pd.read_sql("SELECT sku, source, date, price FROM price_daily", con)
text = pd.read_sql("SELECT category, sku, ts, title, body, is_canonical, score_fast, score_judge, genre FROM text_event", con)
sku_map = pd.read_sql("SELECT sku, category FROM sku_map", con)
for cat in ["RAM", "SSD", "GPU", "COMMODITY"]:
    f = features.build(cat, price, text, sku_map)
    f.to_csv(out / f"features_{cat}.csv", index=False)
    print(cat, f.shape, "weeks", f["week"].min().date() if len(f) else None, "->", f["week"].max().date() if len(f) else None)
