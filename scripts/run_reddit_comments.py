"""Reddit comments (Arctic Shift body= search) -> text_event. Slow; run in background."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, free_sources as fs
con = schema.connect()
since = sys.argv[1] if len(sys.argv) > 1 else "2024-09-01"
plan = {"RAM": (["hardware", "buildapc", "buildapcsales"], ["DDR5 price", "RAM prices", "DDR4 price"]),
        "SSD": (["hardware", "buildapc", "DataHoarder", "buildapcsales"], ["SSD price", "NVMe price"])}
for cat, (subs, qs) in plan.items():
    for sub in subs:
        for q in qs:
            c = fs.reddit_comments(sub, q, after=since, max_pages=40)
            print(cat, sub, q, len(c), "->", schema.upsert_many(con, "text_event", fs.reddit_comment_rows(c, sub, cat)), flush=True)
            time.sleep(2)
