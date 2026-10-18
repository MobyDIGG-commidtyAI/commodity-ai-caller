"""Gate before the refresh job publishes: the exports the API reads must load. Exit 1 names what is broken.
usage: python scripts/check_export.py [data/export]"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd
from cai.assessment import validate_snapshot
from cai.fairvalue import FairValue

export = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "export"
try:
    fv = FairValue(pd.read_parquet(export / "price_daily.parquet"))
    assert {"DDR4", "DDR5"} <= set(fv.models), f"fair-value models missing: {sorted(fv.models)}"
    validate_snapshot(json.loads((export / "assessment_snapshot.json").read_text()))
except Exception as exc:
    sys.exit(f"export check failed: {type(exc).__name__}: {exc}")
print(f"export ok: chip data to {fv.chip.index[-1]:%Y-%m-%d}")
