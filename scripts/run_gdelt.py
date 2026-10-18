"""GDELT DOC 2.0 article list -> text_event (title only; artlist mode has no tone, score_fast stays empty).
Run from your own machine (api.gdeltproject.org times out from the sandbox)."""
import sys, pathlib, json, hashlib, time, requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema
con = schema.connect()
UA = {"User-Agent": "commodity-ai-hackathon/0.1"}
queries = {"RAM": ['"DRAM price"', '"DDR5 price"', '"memory prices" DRAM', '"SK hynix" price', '"Micron" DRAM price'],
           "SSD": ['"NAND price"', '"SSD price"', '"NAND flash" price'],
           "GPU": ['"GPU price"', '"graphics card" price']}
MAX_CONSECUTIVE_SKIPS = 3   # GDELT throttles per IP; if it's refusing us, stop instead of backing off for hours
skips = 0
for cat, qs in queries.items():
    if skips >= MAX_CONSECUTIVE_SKIPS:
        break
    for q in qs:
        if skips >= MAX_CONSECUTIVE_SKIPS:
            break
        # DOC API only allows ~3 months per call -> walk in 90-day windows
        import datetime as dt
        end = dt.datetime.now(dt.UTC).replace(tzinfo=None); rows = []
        for _ in range(9):
            if skips >= MAX_CONSECUTIVE_SKIPS:
                print(f"stopping: {skips} consecutive windows rate-limited; retry later or from another network", flush=True)
                break
            start = end - dt.timedelta(days=90)
            r = None
            for attempt in range(5):   # 429 = rate limited -> back off instead of silently dropping the window
                try:
                    r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc",
                                     params={"query": q + " sourcelang:english", "mode": "artlist", "maxrecords": 250, "format": "json",
                                             "startdatetime": start.strftime("%Y%m%d%H%M%S"), "enddatetime": end.strftime("%Y%m%d%H%M%S")},
                                     headers=UA, timeout=90)
                except requests.RequestException:
                    r = None
                if r is not None and r.status_code == 200:
                    break
                time.sleep(20 * (attempt + 1))
            if r is None or r.status_code != 200:
                skips += 1
                print(f"  skip window {start:%F}..{end:%F} ({getattr(r, 'status_code', 'no response')})", flush=True)
            elif not r.text.lstrip().startswith("{"):
                print(f"  non-JSON reply {start:%F}: {r.text[:120]!r}", flush=True)
            else:
                skips = 0
                for a in r.json().get("articles", []):
                    ts = a["seendate"]  # 20260101T120000Z
                    rows.append({"event_id": hashlib.sha1(("gdelt|" + a["url"]).encode()).hexdigest(), "sku": None, "category": cat,
                                 "ts": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:00+00:00", "source": "gdelt",
                                 "external_id": a["url"], "url": a["url"], "title": a.get("title", ""), "body": "",
                                 "lang": a.get("language", "English"), "cluster_id": None, "is_canonical": 1,
                                 "score_fast": None, "score_judge": None, "meta": json.dumps({"domain": a.get("domain"), "query": q})})
            end = start; time.sleep(6)   # GDELT asks for <= 1 req / 5s
        print(cat, q, "->", schema.upsert_many(con, "text_event", rows), flush=True)
