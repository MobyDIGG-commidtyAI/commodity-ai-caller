import pandas as pd
import pytest

from cai import deals


@pytest.mark.parametrize("title, gb, kind, price", [
    ("[RAM] G.Skill Flare X5 32GB (2x16GB) DDR5 6000 CL30 - $94.99", 32, "DDR5", 94.99),
    ("[RAM] Corsair Vengeance LPX 16GB (2 x 8GB) DDR4 3200 Desktop Memory - $32.99", 16, "DDR4", 32.99),
    ("[RAM] Crucial 2x32GB DDR5 SODIMM 5600 - $189", 64, "DDR5", 189.0),
])
def test_parse_ram(title, gb, kind, price):
    p = deals.parse_ram(title)
    assert (p["capacity_gb"], p["type"], p["price_usd"]) == (gb, kind, price)
    assert p["usd_per_unit"] == pytest.approx(price / gb, abs=1e-3)


@pytest.mark.parametrize("title", [
    "[RAM] Prebuilt desktop PC with 32GB DDR5 - $999",      # not a RAM kit
    "[RAM] 32GB DDR5 6000 kit $10 off",                     # $/GB outside sane bounds
    "[RAM] 32GB kit - $90",                                  # no DDR generation
])
def test_parse_ram_rejects(title):
    assert deals.parse_ram(title) is None


@pytest.mark.parametrize("title, tb, iface", [
    ("[SSD] Crucial P3 PCIe Gen3 NVMe 1TB SSD CT1000P3SSD8 - $89.99", 1.0, "NVME"),
    ("[SSD] Kingston FURY Renegade G5 4096GB NVMe SSD $699.99", 4.096, "NVME"),
    ('[SSD] Samsung 870 EVO 2TB 2.5" SATA - $119', 2.0, "SATA"),
])
def test_parse_ssd(title, tb, iface):
    p = deals.parse_ssd(title)
    assert (p["capacity_tb"], p["type"]) == (tb, iface)


def test_parse_ssd_rejects_portable():
    assert deals.parse_ssd("[SSD] Samsung T7 2TB Portable SSD $129") is None


def test_weekly_series_median_and_min_n():
    d = pd.DataFrame({
        "ts": ["2026-01-05T00:00:00+00:00", "2026-01-06T00:00:00+00:00", "2026-01-07T00:00:00+00:00",
               "2026-01-14T00:00:00+00:00"],
        "category": ["RAM"] * 4, "type": ["DDR5"] * 4, "usd_per_unit": [3.0, 5.0, 100.0, 4.0],
    })
    rows = deals.weekly_series_rows(d, min_n=2)
    assert len(rows) == 1                     # second week has a single deal -> dropped
    assert rows[0]["price"] == 5.0            # median resists the 100 outlier
    assert rows[0]["date"] == "2026-01-11"    # dated at the W-SUN week end


@pytest.mark.parametrize("title, price", [
    ("[RAM] 32GB DDR5 kit $10 off - $89.99", 89.99),
    ("[SSD] WD SN850X 2TB NVMe - $348.99 ($398.99-$50)", 348.99),
    ("[SSD] Save $20: Crucial T500 2TB NVMe $129.99", 129.99),
])
def test_price_skips_discounts(title, price):
    assert deals._price(title) == price
