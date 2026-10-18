"""Comments for matched Reddit posts (highest-engagement first). Resumable via data/raw/comments_done.txt.
usage: python scripts/run_reddit_thread_comments.py [min_comments=10] [max_posts=3000]"""
import sys, pathlib, json, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, reddit_firehose as rf
min_c = int(sys.argv[1]) if len(sys.argv) > 1 else 10
max_posts = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
done_f = rf.ROOT / "data" / "raw" / "comments_done.txt"; done_f.parent.mkdir(parents=True, exist_ok=True)
done = set(done_f.read_text().split()) if done_f.exists() else set()
con = schema.connect()
posts = con.execute("""SELECT external_id, source, category, CAST(json_extract(meta,'$.num_comments') AS INT) n
                       FROM text_event WHERE source LIKE 'reddit:%' AND category IN ('RAM','SSD')
                       AND n >= ? ORDER BY n DESC LIMIT ?""", (min_c, max_posts)).fetchall()
todo = [p for p in posts if p[0] not in done]
print(f"{len(posts)} eligible, {len(todo)} to fetch", flush=True)
total = 0
with done_f.open("a") as fh:
    for i, (pid, src, cat, n) in enumerate(todo, 1):
        sub = src.split(":", 1)[1]
        rows = rf.comment_rows(rf.comments_for_post(pid), sub, cat, pid)
        total += schema.upsert_many(con, "text_event", rows)
        fh.write(pid + "\n"); fh.flush()
        if i % 50 == 0:
            print(f"{i}/{len(todo)} posts, {total} comments", flush=True)
        time.sleep(0.7)
print("comments upserted", total)
