import pytest

from cai.product import parse_title


@pytest.mark.parametrize("title,expected", [
    ("SOM Connect 16GB|32GB DDR4 3200MHz RAM PC ZADAK TWIST CL16 ของแท้ 32GB (16GB x 2)", ("DDR4", 32)),
    ("Corsair Ram PC DDR4 16GB-32GB/3200MHz CL16 Vengeance RGB RT (White) by Banana IT 16GB (2x8GB)", ("DDR4", 16)),
    ("KINGSTON RAM DDR5(6000) 32GB (16GBX2) FURY BEAST RGB (KF560C40BBAK2-32) 32GB (2x16GB)", ("DDR5", 32)),
    ("Kingston Fury Beast DDR5 Noir 5200MHz 128GB Kit (4x32GB) CL40", ("DDR5", 128)),
    ("SOM Connect 8GB|16GB DDR4 3200MHz RAM ZADAK SPARK ของแท้ 8GB", ("DDR4", 8)),
    ("แรม G.Skill Trident Z5 RGB DDR5 6000 64GB", ("DDR5", 64)),
])
def test_ram_titles(title, expected):
    p = parse_title(title)
    assert (p["category"], p["generation"], p["capacity_gb"]) == ("RAM", *expected)


@pytest.mark.parametrize("title,model", [
    ("TVBESTBUY การ์ดจอ GALAX VGA GTX 1630 EX (1-Click OC) 4GB GDDR6 64-bit by Banana IT", "GTX 1630"),
    ("ASUS VGA TUF-GTX1660TI-O6G-EVO-GAMING 6GB GDDR6 192-bit", "GTX 1660 TI"),
    ("การ์ดจอ Sapphire Pulse Radeon RX 6600 XT 8GB GDDR6", "RX 6600 XT"),
    ("MSI GeForce RTX 5090 32G SUPRIM", "RTX 5090"),
])
def test_gpu_titles(title, model):
    assert parse_title(title) == {"category": "GPU", "model": model}


@pytest.mark.parametrize("title", [
    "Samsung SSD M.2 2TB",
    "เสื้อยืด cotton 100%",
    "Notebook ASUS Vivobook 16GB DDR5",       # a laptop, not a RAM kit
    "Mini PC board LPDDR5 16GB onboard",      # soldered memory, not a kit
    "",
])
def test_not_covered(title):
    assert parse_title(title) is None


@pytest.mark.parametrize("title,expected", [
    ("Samsung 990 PRO NVMe SSD 2TB", {"category": "SSD", "interface": "NVME", "capacity_gb": 2000}),
    ("Crucial MX500 SATA SSD 500GB", {"category": "SSD", "interface": "SATA", "capacity_gb": 500}),
    ("M.2 SATA SSD 1TB", {"category": "SSD", "interface": "SATA", "capacity_gb": 1000}),
    ("PCIe SSD 1.5TB", {"category": "SSD", "interface": "NVME", "capacity_gb": 1500}),
    ("Graphics card RTX 4070 Ti Super 16GB", {"category": "GPU", "model": "RTX 4070 TI SUPER"}),
])
def test_new_product_types(title, expected):
    assert parse_title(title) == expected
