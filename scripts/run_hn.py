"""Hacker News stories + comments about memory/storage prices -> text_event."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, free_sources as fs
con = schema.connect()
since = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
q = {"RAM": ["DRAM prices", "DDR5 price", "RAM prices", "memory prices", "SK hynix", "Micron DRAM"],
     "SSD": ["SSD prices", "NAND prices", "NVMe price", "flash memory shortage"],
     "GPU": ["GPU prices", "graphics card prices"]}
for cat, qs in q.items():
    print(cat, schema.upsert_many(con, "text_event", fs.hn_rows(qs, since=since, category=cat)))
