"""Arctic Shift backfill: per category subs x product keywords, since <date>. Free."""
import sys, pathlib, yaml, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, text_sources as ts

root = pathlib.Path(__file__).resolve().parents[1]
since = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
cats = sys.argv[2].split(",") if len(sys.argv) > 2 else ["RAM", "SSD"]
max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 5
skus = yaml.safe_load((root / "config" / "sku_map.yaml").read_text())
con = schema.connect()
queries = {"RAM": ["DDR5 price", "DDR4 price", "RAM prices"],
           "SSD": ["SSD price", "NVMe price", "SSD prices"],
           "GPU": ["GPU price", "GPU prices"]}
total = 0
for cat in cats:
    for sub in ts.DEFAULT_SUBS[cat]:
        for q in queries[cat]:
            posts = ts.arctic_shift_posts(sub, q, after=since, max_pages=max_pages)
            total += schema.upsert_many(con, "text_event", ts.reddit_to_text_rows(posts, sub, cat))
            print(f"{cat} r/{sub} '{q}': {len(posts)}"); time.sleep(1)
        for s in [x for x in skus if x["category"] == cat]:
            posts = ts.arctic_shift_posts(sub, s["keywords"][0], after=since, max_pages=max(1, max_pages // 2))
            total += schema.upsert_many(con, "text_event", ts.reddit_to_text_rows(posts, sub, cat, sku=s["sku"]))
            time.sleep(1)
print("upserted", total)
