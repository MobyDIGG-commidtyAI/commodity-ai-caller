import pytest

from cai import genre

CFG = genre.load_config()


@pytest.mark.parametrize("source,url,title,meta,expected", [
    ("reddit:buildapc", None, "RAM prices", {}, ("social_network", "source:reddit")),
    ("reddit_c:hardware", None, "", {}, ("social_network", "source:reddit_c")),
    ("hn", "https://www.tomshardware.com/x", "DRAM prices", {}, ("social_network", "source:hn")),
    ("trendforce", "https://www.trendforce.com/news/x", "Spot price", {}, ("industry_news", "source:trendforce")),
    ("chin:fmp-stock-news", "https://www.globenewswire.com/x", "Micron to invest", {"publisher": "globenewswire"},
     ("industry_news", "wire:globenewswire")),
    ("chin:fmp-stock-news", "https://www.globenewswire.com/y", "Hudbay Announces Third Quarter 2022 Results",
     {"publisher": "globenewswire"}, ("general_news", "wire-offtopic:globenewswire")),
    ("chin:fmp-stock-news", "https://www.benzinga.com/x", "Micron stock", {"publisher": "benzinga"},
     ("general_news", "publisher:benzinga")),
    ("chin:google-news-rss-recent", None, "Memory chip shortage deepens - Tom's Hardware", {},
     ("industry_news", "publisher:Tom's Hardware")),
    ("chin:google-news-rss-recent", None, "Analyst sets Nvidia target - TheStreet", {},
     ("general_news", "publisher:TheStreet")),
    ("gdelt", "https://www.digitimes.com/news/a.html", "DRAM", {}, ("industry_news", "domain:digitimes.com")),
    ("gdelt", "https://finance.yahoo.com/x", "Micron", {}, ("general_news", "domain:finance.yahoo.com")),
    ("chin:financial-news-2024", None, "Nvidia rallies", {"publisher": "financial-news-2024"}, ("general_news", "default")),
])
def test_classify(source, url, title, meta, expected):
    assert genre.classify(source, url, title, meta, CFG) == expected


def test_meta_as_json_string_and_tag_rows():
    rows = [{"event_id": "a", "source": "chin:x", "url": None, "title": "t", "meta": '{"publisher": "Wccftech"}'}]
    assert genre.tag_rows(rows, CFG) == [{"event_id": "a", "genre": "industry_news", "genre_rule": "publisher:Wccftech"}]


def test_every_genre_is_one_of_three():
    assert set(genre.GENRES) == {"social_network", "industry_news", "general_news"}
