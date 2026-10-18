"""Normalize repeated news so the sentiment model sees 1 event, not 40 reposts.

Pipeline (cheap -> expensive):
  1. exact   : sha1 of normalized title+body
  2. near    : MinHash LSH over word 3-shingles (datasketch), Jaccard >= NEAR_T
  3. window  : only merge items within WINDOW_H hours of each other
  4. (optional, later) embedding cosine >= 0.85 for fully rewritten articles — add when พี่เบิร์ด picks an embedder

Result: text_event.cluster_id + is_canonical. Downstream rule:
  daily_sentiment = weighted mean over CANONICAL items only;
  cluster_size is a separate "attention" feature, never multiplied into polarity.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime

from datasketch import MinHash, MinHashLSH

NEAR_T = 0.6
WINDOW_H = 72
NUM_PERM = 128
TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\d{1,2}:\d{2}", " ", text)          # times
    text = re.sub(r"[^\w\s$%.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def shingles(text: str, k: int = 3) -> set[str]:
    toks = TOKEN_RE.findall(text)
    if len(toks) < k:
        return set(toks)
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def _mh(text: str) -> MinHash:
    m = MinHash(num_perm=NUM_PERM)
    for s in shingles(text):
        m.update(s.encode())
    return m


def cluster(rows: list[dict]) -> list[dict]:
    """rows: dicts with event_id, ts, title, body. Mutates & returns rows with cluster_id/is_canonical."""
    # 1. exact
    exact: dict[str, str] = {}
    for r in rows:
        norm = normalize(f"{r.get('title','')} {r.get('body','')}")
        r["_norm"] = norm
        h = hashlib.sha1(norm.encode()).hexdigest()
        r["cluster_id"] = exact.setdefault(h, r["event_id"])

    # 2+3. near-dup within time window
    lsh = MinHashLSH(threshold=NEAR_T, num_perm=NUM_PERM)
    mh_by_id, ts_by_id = {}, {}
    rows_sorted = sorted(rows, key=lambda r: r["ts"])
    for r in rows_sorted:
        if r["cluster_id"] != r["event_id"]:
            continue                                      # already an exact dup
        m = _mh(r["_norm"])
        t = datetime.fromisoformat(r["ts"])
        best = None
        for cand in lsh.query(m):
            dt = abs((t - ts_by_id[cand]).total_seconds()) / 3600
            if dt <= WINDOW_H:
                best = cand
                break
        if best:
            r["cluster_id"] = best
        else:
            lsh.insert(r["event_id"], m)
            mh_by_id[r["event_id"]] = m
            ts_by_id[r["event_id"]] = t

    # propagate: exact dups of a near-dup follow their parent's cluster
    parent = {r["event_id"]: r["cluster_id"] for r in rows}
    def root(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    sizes = defaultdict(int)
    for r in rows:
        r["cluster_id"] = root(r["event_id"])
        sizes[r["cluster_id"]] += 1
    for r in rows:
        r["is_canonical"] = 1 if r["cluster_id"] == r["event_id"] else 0
        r["cluster_size"] = sizes[r["cluster_id"]]
        r.pop("_norm", None)
    return rows
