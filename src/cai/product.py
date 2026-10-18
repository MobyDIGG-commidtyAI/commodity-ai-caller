"""Shop listing title -> what product it is (RAM generation + kit capacity, or GPU model).

Used by the API and mirrored in commodity-ai-extension extension/parse.js (keep the two in step).
Thai marketplace titles mix Thai and English and often list several variants
("16GB|32GB DDR4 ... 32GB (16GBx2)"); the selected variant is usually the last capacity in the title.
"""
from __future__ import annotations

import re

GPU_RE = re.compile(r"\b(RTX|GTX)\s*-?\s*(\d{4})\s*(TI\s*SUPER|TI|SUPER)?\b|\bRX\s*-?\s*(\d{3,4})\s*(XTX|XT)?\b", re.I)
GPU_HINT_RE = re.compile(r"การ์ดจอ|graphics card|\bVGA\b|GDDR\d|geforce|radeon", re.I)
RAM_GEN_RE = re.compile(r"(?<!G)(?<!LP)DDR\s*([45])(?!\d)", re.I)    # skips GDDR5 (GPU memory) and LPDDR5 (soldered)
RAM_SKIP_RE = re.compile(r"\b(notebook|laptop|ssd|nvme|motherboard|mainboard|cpu|usb|flash ?drive|case)\b|โน้ตบุ๊ค|เมนบอร์ด", re.I)
RANGE_RE = re.compile(r"\d{1,3}\s*GB\s*[-|/]\s*(?=\d{1,3}\s*GB)", re.I)  # "16GB-32GB" / "8GB|16GB" option lists
KIT_RE = re.compile(r"(\d)\s*[x×]\s*(\d{1,3})\s*GB|(\d{1,3})\s*GB\s*[x×]\s*(\d)", re.I)
GB_RE = re.compile(r"(?<![x×\d])(\d{1,3})\s*GB\b", re.I)
VALID_GB = {4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256}
SSD_CAP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(TB|GB)\b", re.I)
SSD_SKIP_RE = re.compile(r"\b(enclosure|external|portable|usb|laptop|notebook|prebuilt|bundle|combo|motherboard)\b", re.I)


def ram_capacity(title: str) -> int | None:
    t = RANGE_RE.sub(" ", title)
    kits = [int(a) * int(b) if a else int(c) * int(d) for a, b, c, d in KIT_RE.findall(t)]
    kits = [k for k in kits if k in VALID_GB]
    if kits:
        return kits[-1]
    gbs = [int(g) for g in GB_RE.findall(t) if int(g) in VALID_GB]
    return gbs[-1] if gbs else None


def parse_title(title: str) -> dict | None:
    """-> {"category": "RAM", "generation": "DDR5", "capacity_gb": 32}
        | {"category": "GPU", "model": "RTX 4070 TI"} | None (not something the model covers)."""
    if not title:
        return None
    g = GPU_RE.search(title)
    if g and (GPU_HINT_RE.search(title) or not RAM_GEN_RE.search(title)):
        if g.group(1):
            suffix = re.sub(r"\s+", " ", g.group(3).upper()).replace("TISUPER", "TI SUPER") if g.group(3) else ""
            model = f"{g.group(1).upper()} {g.group(2)}" + (f" {suffix}" if suffix else "")
        else:
            model = f"RX {g.group(4)}" + (f" {g.group(5).upper()}" if g.group(5) else "")
        return {"category": "GPU", "model": model}
    if re.search(r"\b(ssd|nvme|sata)\b", title, re.I) and not SSD_SKIP_RE.search(title):
        nvme = bool(re.search(r"\b(nvme|pcie|pci-e)\b", title, re.I))
        sata = bool(re.search(r"\bsata\b", title, re.I))
        caps = [float(n) * (1000 if u.upper() == "TB" else 1) for n, u in SSD_CAP_RE.findall(title)]
        caps = [int(n) for n in caps if n.is_integer() and 120 <= n <= 16000]
        # M.2 is a form factor, not an interface.
        if caps and nvme != sata:
            return {"category": "SSD", "interface": "NVME" if nvme else "SATA", "capacity_gb": caps[-1]}
    m = RAM_GEN_RE.search(title)
    if m and not RAM_SKIP_RE.search(title) and not GPU_HINT_RE.search(title):
        cap = ram_capacity(title)
        if cap:
            return {"category": "RAM", "generation": f"DDR{m.group(1)}", "capacity_gb": cap}
    return None
