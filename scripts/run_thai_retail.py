"""Daily THB snapshot for skus that have thai_urls in sku_map.yaml."""
import sys, pathlib, yaml
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, thai_retail
root = pathlib.Path(__file__).resolve().parents[1]
skus = yaml.safe_load((root / "config" / "sku_map.yaml").read_text())
con = schema.connect(); rows = []
for s in skus:
    for retailer, url in (s.get("thai_urls") or {}).items():
        r = thai_retail.snapshot(s["sku"], retailer, url)
        print(s["sku"], retailer, r["price"] if r else "FAILED (fix selector)")
        if r: rows.append(r)
print("upserted", schema.upsert_many(con, "price_daily", rows))
