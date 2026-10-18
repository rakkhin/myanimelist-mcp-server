from tools.tools import (
    normalize_episode_window,
    parse_mal_news_rss,
)


def test_normalize_episode_window_clamps_to_positive_reasonable_range():
    assert normalize_episode_window(-5) == 1
    assert normalize_episode_window(0) == 1
    assert normalize_episode_window(36) == 36
    assert normalize_episode_window(24 * 30) == 24 * 14


def test_parse_mal_news_rss_keeps_item_with_empty_link():
    rss = """<rss version="2.0"><channel>
  <item><title>Title only</title><link/></item>
</channel></rss>"""

    result = parse_mal_news_rss(rss, limit=5)

    assert result["articles"][0]["title"] == "Title only"
    assert result["articles"][0]["url"] is None


def test_parse_mal_news_rss_reads_thumbnail_given_as_element_text():
    # MAL puts the URL in the element text, not in a url attribute.
    rss = """<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
  <item>
    <title>News</title>
    <media:thumbnail> https://cdn.example/thumb.jpg </media:thumbnail>
  </item>
</channel></rss>"""

    result = parse_mal_news_rss(rss, limit=5)

    assert (
        result["articles"][0]["thumbnail"] == "https://cdn.example/thumb.jpg"
    )
