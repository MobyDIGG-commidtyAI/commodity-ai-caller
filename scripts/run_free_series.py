"""yfinance (commodities + chip makers + FX) and Wikipedia pageviews -> tank. Free, ~1 min."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, free_sources as fs
con = schema.connect()
print("yfinance rows", schema.upsert_many(con, "price_daily", fs.yfinance_rows(period="5y")))
print("wiki rows    ", schema.upsert_many(con, "price_daily", fs.wiki_pageviews_rows(start="20230101")))
