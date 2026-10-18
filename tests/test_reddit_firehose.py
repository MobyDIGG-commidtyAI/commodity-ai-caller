import pytest

from cai import reddit_firehose as rf


@pytest.mark.parametrize("sub, title, body, flair, expected", [
    ("buildapcsales", "[SSD] Crucial P3 1TB - $89.99", "", "SSD - M.2", "SSD"),
    ("buildapcsales", "[RAM] G.Skill 32GB DDR5 - $94.99", "", None, "RAM"),     # tag used when no flair
    ("buildapcsales", "[Fan] Thermalright TL-C14CW - $3.79", "", "Fan", None),
    ("hardware", "CXMT begins risk production of HBM3E memory", "", None, "RAM"),  # news sub: no price word needed
    ("buildapc", "Why are DDR5 prices so high right now?", "", None, "RAM"),
    ("buildapc", "Fortnite 165fps 4k living room PC", "32GB RAM, 2TB SSD, total $1500", None, None),
])
def test_classify(sub, title, body, flair, expected):
    assert rf.classify(sub, title, body, flair) == expected
