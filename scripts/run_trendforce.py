"""Crawl TrendForce weekly memory spot reports -> tank. Free, no key. ~2-4 min."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, trendforce

since = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
con = schema.connect()
prices, texts = trendforce.crawl(since=since)
print("price rows", schema.upsert_many(con, "price_daily", prices))
print("text rows ", schema.upsert_many(con, "text_event", texts))
