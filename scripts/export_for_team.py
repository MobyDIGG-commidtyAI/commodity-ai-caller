"""Export tank -> data/export/*.csv + *.parquet for พี่ชิน (prices) and พี่เบิร์ด (text)."""
import sys, pathlib, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema

out = pathlib.Path(__file__).resolve().parents[1] / "data" / "export"; out.mkdir(parents=True, exist_ok=True)
con = schema.connect()
import yaml, json
_skus = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / "config" / "sku_map.yaml").read_text())
schema.upsert_many(con, "sku_map", [{"sku": s["sku"], "category": s["category"], "display_name": s["display_name"],
    "keepa_domain": s.get("keepa_domain"), "keepa_asin": s.get("keepa_asin"), "pcpp_id": s.get("pcpp_id"),
    "trendforce_key": s.get("trendforce_key"), "keywords": json.dumps(s.get("keywords", []))} for s in _skus])
price = pd.read_sql("SELECT * FROM price_daily ORDER BY sku, source, date", con)
text = pd.read_sql("SELECT * FROM text_event ORDER BY ts", con)
sku = pd.read_sql("SELECT * FROM sku_map", con)
for name, df in [("price_daily", price), ("text_event", text), ("sku_map", sku)]:
    # text_event grows past GitHub's 100MB file limit as plain CSV -> gzip it (pandas reads .csv.gz directly)
    df.to_csv(out / (f"{name}.csv.gz" if name == "text_event" else f"{name}.csv"), index=False)
    df.to_parquet(out / f"{name}.parquet", index=False)
# headline-level news only (no Reddit, no HN comments): the small file to open when you want "the news"
NEWS_SQL = """SELECT ts, category, genre, source, title, url,
       json_extract(meta, '$.publisher') AS publisher,
       json_extract(meta, '$.chin_sentiment') AS chin_sentiment,
       json_extract(meta, '$.chin_price_pressure') AS chin_price_pressure
FROM text_event
WHERE is_canonical = 1 AND (source IN ('trendforce', 'gdelt') OR source LIKE 'chin:%'
      OR (source = 'hn' AND json_extract(meta, '$.points') IS NOT NULL))
ORDER BY ts"""
news = pd.read_sql(NEWS_SQL, con)
news.to_csv(out / "news_feed.csv", index=False)
print("news_feed.csv", len(news), "rows")
if len(price):
    price["date"] = pd.to_datetime(price["date"])
    wide = price.pivot_table(index="date", columns=["sku", "source"], values="price").resample("W").last()
    wide.columns = ["|".join(c) for c in wide.columns]
    wide.to_csv(out / "price_weekly_wide.csv")
print(price.groupby(["sku", "source"]).agg(n=("price", "size"), first=("date", "min"), last=("date", "max")))
print("\ntext by source:\n", text.groupby("source").size())
