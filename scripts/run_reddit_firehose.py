"""Reddit posts via Arctic Shift listing + local keyword filter (text search is down). Resumable.
usage: python scripts/run_reddit_firehose.py [sub1,sub2,...]   (default: all in reddit_firehose.SUBS)"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, reddit_firehose as rf
subs = sys.argv[1].split(",") if len(sys.argv) > 1 else list(rf.SUBS)
con = schema.connect()
for sub in subs:
    rf.walk_sub(con, sub, schema.upsert_many, log=lambda m: print(m, flush=True))
