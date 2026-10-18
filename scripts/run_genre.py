"""Tag text_event.genre / genre_rule from config/genres.yaml. Run after every collector (upserts reset the columns)."""
import sys, pathlib, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cai import schema, genre

con = schema.connect()
rows = [dict(zip(["event_id", "source", "url", "title", "meta"], r))
        for r in con.execute("SELECT event_id, source, url, title, meta FROM text_event")]
tags = genre.tag_rows(rows)
con.executemany("UPDATE text_event SET genre=?, genre_rule=? WHERE event_id=?",
                [(t["genre"], t["genre_rule"], t["event_id"]) for t in tags])
con.commit()
print(dict(collections.Counter(t["genre"] for t in tags)))
