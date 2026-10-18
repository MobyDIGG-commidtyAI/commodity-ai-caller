import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from cai.assessment import (
    SnapshotStore, assess, benchmark, build_snapshot, history, outlook,
    seasonal, validate_snapshot, write_snapshot,
)
from cai.product import parse_title

ASOF = pd.Timestamp("2026-10-17")


@pytest.fixture
def snapshot():
    history_rows = [{"date": "2026-10-16", "value": 2.5, "ema": 2.5, "observed": 2.5, "filled": False, "long_gap": False}]
    asset = {
        "category": "RAM", "label": "DDR4", "unit": "USD/GB",
        "last_observed_date": "2026-10-16", "observed_days": 24, "history": history_rows,
        "sources": ["memradar"], "channels": {"retail": [{"date":"2026-10-16","value":2.5}], "deal":[]},
        "reference": {"value": 2.5, "low": 2., "high": 3., "confidence": "medium", "validation":{"n":24}},
        "seasonality": {"status":"historical_only","label":"No clear seasonal pattern","profile":[],"years":1,"validation":[],"reason":"Insufficient years"},
        "outlook": {"status":"scenario","label":"Broadly stable","reason":"Illustrative scenario — not a validated forecast",
                    "validation":[], "points":[{"date":"2027-01-15","value":2.5,"low":2.,"high":3.,"days":90}]},
    }
    return {"schema_version":2,"dataset_version":"fixture-v2","asof":"2026-10-17",
            "fx":{"date":"2026-10-16","rate":35.},"assets":{"RAM_DDR4":asset},"limitations":["US reference, not Thai matched products."]}


@pytest.mark.parametrize("price,status", [(900,"below"),(1400,"within"),(2000,"above"),(None,"insufficient")])
def test_assessment_price_classification(snapshot,price,status):
    r=assess(snapshot,"DDR4 16GB",price,when=ASOF)
    assert r["price_assessment"]["status"]==status
    assert r["price_assessment"]["reference_thb"]==1400
    assert r["price_assessment"]["low_thb"]==1120
    assert r["dataset_version"]=="fixture-v2"
    assert r["outlook"]["points"][0]["value_thb"]==1400
    assert "US reference" in r["price_assessment"]["basis"]


def test_stale_evidence_allows_provisional_band_comparison_but_not_outlook(snapshot):
    for fxdate, obsdate in [("2026-09-01","2026-10-16"),("2026-10-16","2026-01-01")]:
        d=copy.deepcopy(snapshot);d["fx"]["date"]=fxdate;d["assets"]["RAM_DDR4"]["last_observed_date"]=obsdate
        r=assess(d,"DDR4 16GB",1000,when=ASOF)
        assert r["price_assessment"]["status"]=="below"
        assert r["price_assessment"]["provisional"]
        assert r["price_assessment"]["warnings"]
        assert r["outlook"]["status"]=="unavailable"
        assert r["history"]


def test_ambiguous_variant_needs_explicit_confirmation(snapshot):
    r=assess(snapshot,"DDR4 16GB/32GB",1000,when=ASOF,ambiguous=True)
    assert r["product_match"]["needs_confirmation"]
    assert r["price_assessment"]["status"]=="below"
    assert r["price_assessment"]["provisional"]
    r=assess(snapshot,"DDR4 16GB/32GB",1000,when=ASOF,product="DDR4",capacity_gb=16,confirmed=True)
    assert r["price_assessment"]["status"]=="below"
    assert "confirmed=true" in r["dashboard"]
    assert not r["product_match"]["needs_confirmation"]


def test_missing_band_and_unknown_model_do_not_fabricate_verdict(snapshot):
    snapshot["assets"]["RAM_DDR4"]["reference"]["low"]=None
    assert assess(snapshot,"DDR4 16GB",1,when=ASOF)["price_assessment"]["status"]=="insufficient"
    r=assess(snapshot,"Graphics card RTX 5080",25000,when=ASOF)
    assert r["covered"] and r["price_assessment"]["reference_thb"] is None
    assert r["outlook"]["status"]=="unavailable"
    assert not assess(snapshot,"Cotton T-shirt",200,when=ASOF)["covered"]


def test_ema_fill_is_display_only_and_marks_long_gap():
    obs=pd.Series([10.,20.],index=pd.to_datetime(["2025-01-01","2025-03-01"]))
    rows=history(obs)
    assert len(rows)==60
    assert sum(r["observed"] is not None for r in rows)==2
    assert rows[10]["filled"] and rows[10]["long_gap"]
    assert rows[-1]["ema"]<rows[-1]["value"]


def test_future_data_does_not_change_models():
    dates=pd.date_range("2022-01-31",periods=48,freq="ME")
    obs=pd.Series(2+np.sin(np.arange(48)/4)*.3,index=dates)
    cutoff=pd.Timestamp("2024-12-31")
    extra=pd.concat([obs,pd.Series([1000.],index=[pd.Timestamp("2026-01-01")])])
    assert outlook(obs,cutoff)==outlook(extra,cutoff)
    assert seasonal(obs,cutoff)==seasonal(extra,cutoff)
    empty=pd.Series(dtype=float,index=pd.DatetimeIndex([]))
    assert benchmark(obs,empty,empty,0,cutoff)==benchmark(extra,empty,empty,0,cutoff)


def test_observed_targets_only_and_forecast_gates():
    obs=pd.Series([3.,2.,1.],index=pd.to_datetime(["2025-01-01","2025-04-01","2025-08-01"]))
    r=outlook(obs,ASOF)
    assert r["status"]=="unavailable" and r["points"]==[]
    obs=pd.Series(np.linspace(3,2,12),index=pd.date_range("2025-01-31",periods=12,freq="ME"))
    r=outlook(obs,pd.Timestamp("2025-12-31"))
    assert r["status"]=="scenario"
    assert all(v["n"]<12 for v in r["validation"])


def test_snapshot_atomic_reload_and_last_good_retention(tmp_path,snapshot):
    path=tmp_path/"snapshot.json";store=SnapshotStore(path)
    assert store.load() is None
    write_snapshot(snapshot,path);assert store.load()["dataset_version"]=="fixture-v2"
    new=copy.deepcopy(snapshot);new["dataset_version"]="next"
    write_snapshot(new,path);assert store.load()["dataset_version"]=="next"
    path.write_text('{"schema_version":99}')
    assert store.load()["dataset_version"]=="next" and store.error


def test_future_snapshot_observations_rejected(snapshot):
    snapshot["assets"]["RAM_DDR4"]["history"][0]["date"]="2027-01-01"
    with pytest.raises(ValueError):validate_snapshot(snapshot)


def test_builder_rejects_bad_conditions_and_future_rows(tmp_path):
    pd.DataFrame([
        {"sku":"FX-USDTHB","source":"yfinance","currency":"THB","date":"2026-10-16","price":35.},
        {"sku":"MR-DDR4-USD_PER_GB","source":"memradar","currency":"USD","date":"2026-10-16","price":2.}
    ]).to_parquet(tmp_path/"price_daily.parquet")
    pd.DataFrame([
        {"ts":"2026-10-15","title":"[RAM] DDR4 16GB $40","url":"a"},
        {"ts":"2026-10-15","title":"[RAM] DDR4 16GB refurbished $20","url":"b"},
        {"ts":"2026-10-18","title":"[RAM] DDR4 16GB $30","url":"c"},
        {"ts":"2026-10-15","title":"[SSD] M.2 SSD 1TB $50","url":"d"},
    ]).to_csv(tmp_path/"deals_bapcs.csv",index=False)
    result=build_snapshot(tmp_path,ASOF)
    assert len(result["assets"]["RAM_DDR4"]["channels"]["deal"])==1
    assert result["audit"]["rejected_count"]==3
    assert {r["reason"] for r in result["audit"]["rejected"]}>={"future_date","condition_or_bundle","ambiguous_product"}




def test_validated_forecast_uses_forecast_specific_residuals():
    dates=pd.date_range("2020-01-31",periods=72,freq="ME")
    obs=pd.Series(np.exp(np.arange(72)*.035),index=dates)
    result=outlook(obs,dates[-1])
    assert result["status"]=="validated_forecast"
    report=result["validation"][-1]
    assert report["n"]>=12 and report["mae"]<=.95*report["carry_mae"]
    assert "forecast residual" in result["interval_kind"]
    assert result["points"][-1]["low"]<=result["points"][-1]["value"]<=result["points"][-1]["high"]




def test_first_capacity_is_automatic_but_price_ranges_still_require_confirmation(snapshot):
    result=assess(snapshot,"DDR4 16GB/32GB",1500,when=ASOF,variant_title="RAM DDR4 16GB",variant_label="16GB")
    assert result["product"]["capacity_gb"]==16
    assert not result["product_match"]["needs_confirmation"]
    assert result["product_match"]["confirmed"] is False
    assert "Selected shop option" in result["product_match"]["selection_note"]
    assert result["price_assessment"]["reference_thb"]==16*2.5*snapshot["fx"]["rate"]
    assert assess(snapshot,"DDR4 16GB/32GB",1500,when=ASOF,ambiguous=True)["product_match"]["needs_confirmation"]
    assert assess(snapshot,"DDR4 16GB/32GB",1500,when=ASOF,confirmed=True,product="DDR4",capacity_gb=32)["product"]["capacity_gb"]==32


def test_first_capacity_preserves_kit_and_ssd_units(snapshot):
    assert assess(snapshot,"DDR4 16GBx2 64GB",when=ASOF,variant_title="DDR4 16GBx2",variant_label="16GBx2")["product"]["capacity_gb"]==32
    assert assess(snapshot,"SATA SSD 250GB 500GB 1TB 2TB",when=ASOF,variant_title="SATA SSD 500GB",variant_label="WDS500G3B0B")["product"]["capacity_gb"]==500
    assert assess(snapshot,"NVMe SSD 1TB/2TB",when=ASOF,variant_title="NVMe SSD 1TB",variant_label="1TB")["product"]["capacity_gb"]==1000
    assert assess(snapshot,"SATA SSD 250GB 500GB 1TB 2TB",when=ASOF)["product_match"]["needs_confirmation"]
    assert assess(snapshot,"DDR4/DDR5 16GB 32GB",when=ASOF)["product_match"]["needs_confirmation"]


def test_selected_variant_provenance_survives_receipt_link(snapshot):
    r=assess(snapshot,"SATA SSD 250GB 500GB 1TB",4950,when=ASOF,
             variant_title="SATA SSD 500GB",variant_label="WDS500G3B0B",price_source="selected_option_price")
    assert r["product"]["capacity_gb"]==500
    assert "variant_title=SATA+SSD+500GB" in r["dashboard"]
    assert not r["product_match"]["confirmed"]
    with pytest.raises(ValueError):
        assess(snapshot,"DDR4 16GB",when=ASOF,variant_title="NVMe SSD 1TB",variant_label="1TB")


def test_price_band_comparison_is_independent_of_other_sections(snapshot):
    asset=snapshot["assets"]["RAM_DDR4"]
    asset["outlook"]={"status":"unavailable","label":"Uncertain","reason":"No forecast","points":[],"validation":[]}
    asset["seasonality"]["status"]="historical_only"
    asset["reference"]["validation"]={"n":0}
    r=assess(snapshot,"DDR4 16GB",1400,when=ASOF)
    assert r["price_assessment"]["status"]=="within"
    assert not r["price_assessment"]["provisional"]
    assert r["outlook"]["status"]=="unavailable"
    assert r["seasonality"]["status"]=="historical_only"
    asset["reference"]["low"]=None
    r=assess(snapshot,"DDR4 16GB",1400,when=ASOF)
    assert r["price_assessment"]["label"]=="No reference range"
