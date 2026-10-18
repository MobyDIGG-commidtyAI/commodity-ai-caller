"""Fill keepa_asin in config/sku_map.yaml via Keepa search (costs ~10 tokens per sku). Prints candidates; you pick."""
import sys, pathlib, yaml
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai.keepa_client import KeepaClient

cfg = pathlib.Path(__file__).resolve().parents[1] / "config" / "sku_map.yaml"
skus = yaml.safe_load(cfg.read_text())
kc = KeepaClient()
for s in skus:
    if s.get("keepa_asin"):
        continue
    print(f"\n== {s['sku']}  ({s['display_name']})")
    for c in kc.search(s["display_name"], domain=s.get("keepa_domain", 1), limit=5):
        print(f"   {c['asin']}  {c['brand']}  {(c['title'] or '')[:90]}")
    print(f"   tokens left: {kc.tokens_left}")
