import pytest

from cai.free_sources import hn_relevant


@pytest.mark.parametrize("query, text, keep", [
    ("NAND prices", "High Diesel Prices Bankrupted 16 Trucking Companies", False),   # "and" typo-match
    ("NAND prices", "NAND contract prices up 20% in Q4", True),
    ("NVMe price", "Turn off Apple Intelligence and get its disk space back", False),
    ("NVMe price", "Cheapest 4TB NVMe drive right now?", True),
    ("Micron DRAM", "Micron raises DRAM guidance", True),
    ("Micron DRAM", "Micron Technology earnings call", False),
    ("DDR5 price", "DDR4 kits are cheaper than ever", False),
    ("SK hynix", "SK Hynix HBM4 sampling", True),
])
def test_hn_relevant(query, text, keep):
    assert hn_relevant(query, text) is keep
