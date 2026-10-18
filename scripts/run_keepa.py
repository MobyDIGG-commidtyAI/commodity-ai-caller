"""Pull 730 days of Keepa price history for every sku with keepa_asin -> tank. Needs KEEPA_KEY."""
import sys, pathlib, yaml, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema
from cai.keepa_client import KeepaClient

root = pathlib.Path(__file__).resolve().parents[1]
skus = yaml.safe_load((root / "config" / "sku_map.yaml").read_text())
con = schema.connect()
schema.upsert_many(con, "sku_map", [{
    "sku": s["sku"], "category": s["category"], "display_name": s["display_name"],
    "keepa_domain": s.get("keepa_domain"), "keepa_asin": s.get("keepa_asin"),
    "pcpp_id": s.get("pcpp_id"), "trendforce_key": s.get("trendforce_key"),
    "keywords": json.dumps(s.get("keywords", []))} for s in skus])
kc = KeepaClient()
days = int(sys.argv[1]) if len(sys.argv) > 1 else 730
for s in skus:
    if not s.get("keepa_asin"):
        print("skip (no asin):", s["sku"]); continue
    rows = kc.price_history(s["keepa_asin"], s.get("keepa_domain", 1), s["sku"], days=days)
    print(s["sku"], "->", schema.upsert_many(con, "price_daily", rows), "rows | tokens left", kc.tokens_left)
