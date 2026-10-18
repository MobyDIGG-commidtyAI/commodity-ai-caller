"""Pull current hardware news RSS, keep items mentioning our categories."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, text_sources as ts
con = schema.connect()
kws = ["DRAM", "DDR5", "DDR4", "memory price", "NAND", "SSD", "RAM price", "Samsung", "SK hynix", "Micron", "Kingston"]
n = 0
for k, u in ts.RSS_FEEDS.items():
    rows = ts.rss_to_text_rows(k, u, keyword_filter=kws)
    n += schema.upsert_many(con, "text_event", rows); print(k, len(rows))
print("upserted", n)
