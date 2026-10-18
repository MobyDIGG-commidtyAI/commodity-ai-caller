"""r/buildapcsales deal titles -> weekly USD/GB (RAM) and USD/TB (SSD) series in price_daily + data/export/deals_bapcs.csv.
Rerun after run_reddit_firehose.py (idempotent)."""
import sys, pathlib, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, deals
out = pathlib.Path(__file__).resolve().parents[1] / "data" / "export"
con = schema.connect()
text = pd.read_sql("SELECT ts, category, title, url FROM text_event WHERE source='reddit:buildapcsales'", con)
d = deals.parse_deals(text)
print(f"{len(text)} deal posts -> {len(d)} parsed")
if len(d):
    d.sort_values("ts").to_csv(out / "deals_bapcs.csv", index=False)
    print(d.groupby(["category", "type"]).agg(n=("usd_per_unit", "size"), first=("ts", "min"), last=("ts", "max"),
                                               median=("usd_per_unit", "median")))
rows = deals.weekly_series_rows(d)
con.execute("DELETE FROM price_daily WHERE source='reddit_bapcs'")
print("weekly rows", schema.upsert_many(con, "price_daily", rows))
