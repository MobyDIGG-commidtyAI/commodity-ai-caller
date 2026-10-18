import json

from cai import partner_chin

MEMRADAR = """# MemRadar Price Index: notes
#
segment,month,median_price_usd,median_usd_per_gb,product_count
ddr5,2026-08,300.00,9.5000,40
ddr4,2026-09,120.00,4.0000,30
nvme_ssd,2026-09,90.00,0.0900,50
"""

ARTICLES = """published_at,product,title,source,url,sentiment,price_pressure,has_up_signal,has_down_signal,source_dataset
2026-10-13 18:11:37+00:00,ram,DRAM contract prices jump,benzinga,,0.5,0.8,1,0,google-news-rss-recent
2026-10-13 18:11:37+00:00,ram,DRAM contract prices jump,benzinga,,0.5,0.8,1,0,google-news-rss-recent
2026-10-13 18:11:37+00:00,gpu,Nvidia ships new card,benzinga,https://x/1,,,0,0,fmp-stock-news
2026-10-13 18:11:37+00:00,stock,Unrelated,benzinga,,,,0,0,fmp-stock-news
"""


def test_month_end():
    assert partner_chin.month_end("2026-09") == "2026-09-30"
    assert partner_chin.month_end("2024-02") == "2024-02-29"


def test_memradar_skips_comments_and_dates_at_month_end(tmp_path):
    f = tmp_path / "mr.csv"; f.write_text(MEMRADAR)
    rows = {r["sku"]: r for r in partner_chin.memradar_rows(f)}
    assert set(rows) == {"MR-DDR5-USD_PER_GB", "MR-DDR4-USD_PER_GB", "MR-NVME-USD_PER_GB"}
    assert rows["MR-DDR5-USD_PER_GB"]["date"] == "2026-08-31"
    assert rows["MR-DDR4-USD_PER_GB"]["price"] == 4.0
    assert json.loads(rows["MR-NVME-USD_PER_GB"]["meta"])["product_count"] == 50


def test_articles_ids_are_stable_and_dupes_collapse(tmp_path):
    f = tmp_path / "a.csv"; f.write_text(ARTICLES)
    a, b = partner_chin.article_rows(f), partner_chin.article_rows(f)
    assert [r["event_id"] for r in a] == [r["event_id"] for r in b]
    assert len(a) == 2                                   # exact dup collapsed, unknown product dropped
    ram = next(r for r in a if r["category"] == "RAM")
    assert ram["source"] == "chin:google-news-rss-recent"
    assert ram["ts"] == "2026-10-13T18:11:37+00:00"       # same format as the rest of text_event
    assert "score_fast" not in ram                       # reserved for the sentiment model
    assert json.loads(ram["meta"])["chin_price_pressure"] == 0.8


def test_contract_series(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({
        "ram": {"ddr5": [{"month": "2026-09", "index": 120.5, "usdPerGb": 9.1, "products": 40}]},
        "gpuRetail": [{"month": "2026-09", "index": 116.4, "matchedModels": 473}],
        "gpuWeekly": {"nvidia-rtx-4090": [{"date": "2026-10-12", "medianUsdPerHour": 0.5}]}}))
    rows = {r["sku"]: r for r in partner_chin.contract_rows(f)}
    assert rows["MR-DDR5-INDEX"]["currency"] == "INDEX"
    assert rows["GPX-GPU-RETAIL-INDEX"]["date"] == "2026-09-30"
    assert rows["CLOUD-RTX4090-USD_PER_HR"]["price"] == 0.5
