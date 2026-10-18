"""RUN FROM YOUR OWN MACHINE (web.archive.org is blocked in the sandbox).
Wayback snapshots of product pages -> price_daily. Denser than Common Crawl for Amazon pages.
usage: python scripts/run_wayback.py 2024-01-01"""
import sys, pathlib, json, re, time, requests, yaml
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, commoncrawl as cc
root = pathlib.Path(__file__).resolve().parents[1]
since = (sys.argv[1] if len(sys.argv) > 1 else "2024-01-01").replace("-", "")
sku_cfg = yaml.safe_load((root / "config" / "sku_map.yaml").read_text())
con = schema.connect()
UA = {"User-Agent": "commodity-ai-hackathon/0.1"}
targets = {}
for s in sku_cfg:                                   # thai_urls + amazon dp pages if asin known
    for retailer, url in (s.get("thai_urls") or {}).items():
        targets[(s["sku"], retailer)] = url
    if s.get("keepa_asin"):
        targets[(s["sku"], "amazon")] = f"https://www.amazon.com/dp/{s['keepa_asin']}"
for (sku, retailer), url in targets.items():
    r = requests.get("https://web.archive.org/cdx/search/cdx", params={"url": url, "output": "json", "from": since,
                     "filter": "statuscode:200", "collapse": "timestamp:8", "fl": "timestamp,original"}, headers=UA, timeout=60)
    rows = []
    for ts, orig in (r.json()[1:] if r.status_code == 200 else []):
        snap = requests.get(f"https://web.archive.org/web/{ts}id_/{orig}", headers=UA, timeout=60)
        title, price, cur = cc.extract_price(snap.text)
        if price and price > 100:
            rows.append({"sku": sku, "source": f"wb_{retailer}", "date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", "price": price,
                         "currency": cur, "meta": json.dumps({"url": orig, "wb_ts": ts})})
        time.sleep(0.5)
    print(sku, retailer, "->", schema.upsert_many(con, "price_daily", rows), flush=True)
