import pytest

from cai import trendforce as tf


def ddr4(body):
    m = tf.DDR4_TO_RE.search(body) or tf.DDR4_AT_RE.search(body)
    return float(m.group(1)) if m else None


@pytest.mark.parametrize("body, expected", [
    # "this week" value, not "last week" (old parser stored 25.407)
    ("(i.e., DDR4 1Gx8 3200MT/s) has increased by 9.64% from US$25.407 last week (Jan.7) to US$27.857 this week.", 27.857),
    # decimals in the % change used to break the old regex (2025-08-06)
    ("(i.e., DDR4 1Gx8 3200MT/s) slightly decreased by -2.40% from US$5.123 last week to US$5.000 this week.", 5.0),
    ("DDR4 1Gx8 2666MT/s saw a price drop of 0.84% (from US$1.904 to US$1.888) this week.", 1.888),
    ("(i.e., DDR4 1Gx8 3200MT/s) has not changed from last week and is holding steady at US$ 46.32 (October 6).", 46.32),
])
def test_ddr4_this_week(body, expected):
    assert ddr4(body) == expected


def test_ddr4_ignores_reball_chips():
    body = ("For 1Gx8 reball chips, the spot price has fallen from around US$1.53 last week to US$1.48 this week. "
            "The average spot price of the mainstream chips (i.e., DDR4 1Gx8 3200MT/s) slightly decreased by -2.40% "
            "from US$5.123 last week to US$5.000 this week.")
    assert ddr4(body) == 5.0


def test_ddr4_does_not_cross_into_nand():
    body = "DDR4 1Gx8 3200MT/s prices moved sideways. Spot prices of 512Gb TLC wafers dropped by 2.90% to US$18.833."
    assert ddr4(body) is None


def test_article_links_accepts_non_standard_titles():
    html = ('<a href="https://www.trendforce.com/news/2024/03/27/insights-dram-and-nand-flash-spot-prices-drop-as-demand-weakens/">x</a>'
            '<a href="https://www.trendforce.com/news/2024/03/28/some-other-story/">y</a>')
    assert tf.article_links(html) == [{"url": "https://www.trendforce.com/news/2024/03/27/"
                                              "insights-dram-and-nand-flash-spot-prices-drop-as-demand-weakens/",
                                       "date": "2024-03-27"}]


def test_list_articles_merges_queries_and_dedupes(monkeypatch):
    link = "<a href=\"https://www.trendforce.com/news/{d}/{slug}/\">x</a>"
    pages = {  # query -> page 1 html; page 2 is empty
        "A": link.format(d="2024/04/03", slug="memory-spot-price-update") + link.format(d="2024/03/20", slug="memory-spot-price-update"),
        "B": link.format(d="2024/04/03", slug="memory-spot-price-update") + link.format(d="2024/03/27", slug="dram-spot-prices-drop"),
    }
    monkeypatch.setattr(tf, "_get", lambda url: pages.get(url.split("s=")[1], "") if "/page/1/" in url else "")
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)
    got = tf.list_articles(max_pages=3, since="2024-01-01", queries=("A", "B"))
    assert [a["date"] for a in got] == ["2024-04-03", "2024-03-27", "2024-03-20"]
