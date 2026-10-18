"""Load a teammate's (พี่ชิน's) news and price archive into the tank.
usage: python scripts/run_partner_chin.py [data/partner/chin-2026-10-15]
The archive is team data and is not in this repo: on GCP it lives in the bucket under partner/ and
scripts/cloud_job.sh copies it here. Without it the step is skipped (rows already in the tank stay)."""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cai import schema, partner_chin

archive = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "partner" / "chin-2026-10-15"
if not archive.is_dir():
    print(f"partner archive not found at {archive}; skipped")
    sys.exit(0)
price, text = partner_chin.load(archive)
con = schema.connect()
print("price_daily", schema.upsert_many(con, "price_daily", price))
print("text_event ", schema.upsert_many(con, "text_event", text))
