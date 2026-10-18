"""Common Crawl monthly snapshots of retailer product pages -> price_daily. Slow + index server flaky: background it.
usage: python scripts/run_commoncrawl.py [retailers=jib,advice,banana] [max_pages_per_crawl=150]"""
import sys, pathlib, yaml, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, commoncrawl as cc
root = pathlib.Path(__file__).resolve().parents[1]
retailers = (sys.argv[1] if len(sys.argv) > 1 else "jib,advice,banana").split(",")
max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 150
sku_cfg = yaml.safe_load((root / "config" / "sku_map.yaml").read_text())
con = schema.connect()
KNOWN = ["CC-MAIN-2026-38","CC-MAIN-2026-33","CC-MAIN-2026-30","CC-MAIN-2026-26","CC-MAIN-2026-21","CC-MAIN-2026-18","CC-MAIN-2026-13","CC-MAIN-2026-08","CC-MAIN-2026-04",
         "CC-MAIN-2025-51","CC-MAIN-2025-47","CC-MAIN-2025-43","CC-MAIN-2025-38","CC-MAIN-2025-33","CC-MAIN-2025-30","CC-MAIN-2025-26","CC-MAIN-2025-21","CC-MAIN-2025-18","CC-MAIN-2025-13","CC-MAIN-2025-08","CC-MAIN-2025-05",
         "CC-MAIN-2024-51","CC-MAIN-2024-46","CC-MAIN-2024-42","CC-MAIN-2024-38","CC-MAIN-2024-33","CC-MAIN-2024-30","CC-MAIN-2024-26","CC-MAIN-2024-22","CC-MAIN-2024-18","CC-MAIN-2024-10"]
crawls = KNOWN   # index server is too flaky to probe; a 404 crawl id just yields 0 rows
print("crawls:", crawls, flush=True)
(root / "data" / "raw" / "cc_crawls.json").write_text(json.dumps(crawls))
for crawl in crawls:
    for r in retailers:
        rows = cc.crawl_retailer(crawl, r, sku_cfg, max_pages=max_pages)
        print(crawl, r, "->", schema.upsert_many(con, "price_daily", rows), flush=True)
