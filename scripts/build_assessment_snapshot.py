"""Build and atomically publish the local v2 evidence snapshot (no deployment)."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cai.assessment import build_snapshot, write_snapshot

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=ROOT / "data/export")
    p.add_argument("--asof", default=None)
    args = p.parse_args()
    data = build_snapshot(args.data_dir, args.asof)
    path = args.data_dir / "assessment_snapshot.json"
    write_snapshot(data, path)
    print(f"Verified snapshot {data['dataset_version']} as of {data['asof']}: {len(data['assets'])} assets")
    for key, asset in data["assets"].items():
        print(f"{key}: {asset['observed_days']} observed days; seasonal={asset['seasonality']['status']}; outlook={asset['outlook']['status']}")
