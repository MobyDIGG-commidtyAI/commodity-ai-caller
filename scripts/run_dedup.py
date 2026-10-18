"""Cluster text_event (exact + MinHash near-dup within 72h). Writes cluster_id / is_canonical back."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, dedup

con = schema.connect()
cur = con.execute("SELECT event_id, ts, title, body FROM text_event")
rows = [dict(zip(["event_id", "ts", "title", "body"], r)) for r in cur.fetchall()]
rows = dedup.cluster(rows)
con.executemany("UPDATE text_event SET cluster_id=?, is_canonical=? WHERE event_id=?",
                [(r["cluster_id"], r["is_canonical"], r["event_id"]) for r in rows])
con.commit()
n = len(rows); c = sum(r["is_canonical"] for r in rows)
print(f"{n} items -> {c} canonical events ({n - c} dups removed, {100*(n-c)/max(n,1):.1f}%)")
