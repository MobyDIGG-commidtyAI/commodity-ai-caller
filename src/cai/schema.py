"""Single "tank" schema for Commodity AI.

Three tables only (descope on purpose):
  sku_map      - one row per tracked product, with every source's identifier
  price_daily  - one row per (sku, source, date)  -> for พี่ชิน (charts / price model)
  text_event   - one row per text item (news, reddit, spot-report narrative),
                 with cluster_id after dedup -> for พี่เบิร์ด (sentiment model)

Storage: SQLite file (data/tank.db). Export to parquet/csv via export.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tank.db"

DDL = """
CREATE TABLE IF NOT EXISTS sku_map (
    sku             TEXT PRIMARY KEY,      -- our stable key, e.g. RAM-DDR5-32GB-2x16-6000
    category        TEXT NOT NULL,         -- RAM | SSD | GPU
    display_name    TEXT NOT NULL,
    keepa_domain    INTEGER,               -- 1=amazon.com 5=co.jp 3=de
    keepa_asin      TEXT,
    pcpp_id         TEXT,
    trendforce_key  TEXT,                  -- which spot series is the upstream proxy
    keywords        TEXT                   -- JSON list used to match text to this sku
);

CREATE TABLE IF NOT EXISTS price_daily (
    sku         TEXT NOT NULL,
    source      TEXT NOT NULL,             -- keepa_amazon | keepa_new | keepa_buybox | trendforce_spot | jib | advice | ...
    date        TEXT NOT NULL,             -- YYYY-MM-DD (UTC)
    price       REAL NOT NULL,
    currency    TEXT NOT NULL,
    meta        TEXT,                      -- JSON (e.g. offers count, url)
    PRIMARY KEY (sku, source, date)
);

CREATE TABLE IF NOT EXISTS text_event (
    event_id    TEXT PRIMARY KEY,          -- sha1(source + external_id)
    sku         TEXT,                      -- nullable: category-level text (e.g. "DRAM market")
    category    TEXT,                      -- RAM | SSD | GPU | MARKET
    ts          TEXT NOT NULL,             -- ISO8601 UTC
    source      TEXT NOT NULL,             -- trendforce | reddit:<sub> | rss:<feed> | pantip
    external_id TEXT,
    url         TEXT,
    title       TEXT,
    body        TEXT,
    lang        TEXT DEFAULT 'en',
    cluster_id  TEXT,                      -- filled by dedup.py (1 cluster = 1 event vote)
    is_canonical INTEGER DEFAULT 1,        -- 1 = representative of its cluster
    score_fast  REAL,                      -- reserved for FinBERT/encoder score (-1..1)
    score_judge REAL,                      -- reserved for LLM-as-judge score (-1..1)
    meta        TEXT,
    genre       TEXT,                      -- social_network | industry_news | general_news (genre.py)
    genre_rule  TEXT                       -- which rule decided the genre, for tracing
);

CREATE INDEX IF NOT EXISTS idx_text_ts ON text_event(ts);
CREATE INDEX IF NOT EXISTS idx_text_cat ON text_event(category, ts);
CREATE INDEX IF NOT EXISTS idx_price_sku ON price_daily(sku, date);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=120)   # several collectors may write concurrently
    try:
        con.execute("PRAGMA journal_mode=WAL")    # persistent once set; may fail if another writer holds a lock
    except sqlite3.OperationalError:
        pass
    con.executescript(DDL)
    have = {r[1] for r in con.execute("PRAGMA table_info(text_event)")}
    for col in ("genre", "genre_rule"):        # tanks created before the genre columns existed
        if col not in have:
            con.execute(f"ALTER TABLE text_event ADD COLUMN {col} TEXT")
    return con


def upsert_many(con: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    con.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    con.commit()
    return len(rows)
