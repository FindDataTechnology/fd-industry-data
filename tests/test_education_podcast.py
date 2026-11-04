"""教育播客层测试（spec cap1 R5/R6/R7 + cap2 R3 的蜘蛛侧断言）。

- 3.1 seed：Apple 目录实调（entity=podcast 拿 feedUrl + feed 实测验证）
- 3.2 poller：file:// 人造 feed——注入新期 → 下回合可见；第二回合只见新期（增量幂等）
- 3.3 health：拔通道 → health_fail 行带 consecutive_fail+reason，绝不报「无新内容」

跑法：pytest tests/test_education_podcast.py -v
"""
from __future__ import annotations

import pytest

RSS_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Edu Cast</title><link>https://example.com</link>
{items}
</channel></rss>"""

ITEM_TMPL = """  <item><title>Ep {n}</title><guid>tag:example.com,2026:ep{n}</guid>
  <pubDate>Mon, 0{min} Nov 2026 08:00:00 +0000</pubDate>
  <enclosure url="https://example.com/audio/ep{n}.mp3" length="100" type="audio/mpeg"/></item>"""


@pytest.fixture()
def feed_file(tmp_path):
    p = tmp_path / "feed.xml"
    p.write_text(RSS_TMPL.format(items=ITEM_TMPL.format(n=1, min=1) + ITEM_TMPL.format(n=2, min=2)))
    return p


@pytest.mark.online
def test_seed_apple_channel_returns_feedurl_and_validates():
    """3.1：seed 实调——feedUrl 必随 entity=podcast 返回，且 feed 实测可解析。"""
    from spiders.education_podcast.spider import run_podcast_seed

    rows = run_podcast_seed(keywords=["history of science"], countries=["us"], limit=2)
    assert rows, "seed returned empty — Apple channel broken"
    for r in rows:
        assert r["feed_url"], f"seed row missing feedUrl: {r['name']}"
        assert r["license"] == "public-enclosure"
        assert r["redistribution"] == "needs_authorization"
        assert r["source_url"], "missing Apple page URL (traceability)"


def test_poller_new_episode_visible_next_round(feed_file, tmp_path):
    """3.2 核心验证：人造新期注入 → 下回合目录可见且带发布时间。"""
    from spiders.education_podcast.spider import run_podcast_poller

    state = tmp_path / "state.json"
    url = feed_file.as_uri()
    rows = run_podcast_poller(feeds=[url], state_file=str(state))
    eps = [r for r in rows if r["kind"] == "episode"]
    assert len(eps) == 2, f"first round should see 2 episodes, got {len(eps)}"
    assert all(e["pub_date"] for e in eps), "episodes must carry publish date"
    assert all(e["enclosure_url"] and e["enclosure_url"].startswith("https://example.com/audio/")
               for e in eps), "enclosure must be the RSS-declared URL"
    assert all(e["body_state"] == "pending" for e in eps), "no transcription → pending, never fake done"

    # 注入人造新期（ep 3），只应出现新期
    feed_file.write_text(RSS_TMPL.format(
        items=ITEM_TMPL.format(n=1, min=1) + ITEM_TMPL.format(n=2, min=2) + ITEM_TMPL.format(n=3, min=3)))
    rows2 = run_podcast_poller(feeds=[url], state_file=str(state))
    eps2 = [r for r in rows2 if r["kind"] == "episode"]
    assert len(eps2) == 1 and eps2[0]["title"] == "Ep 3", f"second round should see only Ep 3, got {[e['title'] for e in eps2]}"


def test_poller_channel_failure_is_visible_not_empty(tmp_path):
    """3.3 核心验证：拔通道 → health_fail（计数+原因），绝不伪装「无新内容」。"""
    from spiders.education_podcast.spider import FAIL_REASONS, run_podcast_poller

    state = tmp_path / "state.json"
    broken = "file:///nonexistent/dead-feed.xml"
    rows = run_podcast_poller(feeds=[broken], state_file=str(state))
    fails = [r for r in rows if r["kind"] == "health_fail"]
    assert len(fails) == 1, f"expected one health_fail row, got rows={rows}"
    assert fails[0]["consecutive_fail"] == 1
    assert fails[0]["reason"] in FAIL_REASONS

    # 二次失败 → 计数递增（连续失败语义）
    rows2 = run_podcast_poller(feeds=[broken], state_file=str(state))
    assert [r for r in rows2 if r["kind"] == "health_fail"][0]["consecutive_fail"] == 2


def test_poller_refuses_empty_feeds():
    """空 feeds 列表拒绝运行（防「全绿空转」）。"""
    from spiders.education_podcast.spider import run_podcast_poller
    import pytest as _pt
    with _pt.raises(RuntimeError):
        run_podcast_poller(feeds=[])


def test_enclosure_only_from_rss_track():
    """spec cap1 R7 负面清单：转录输入只接受 feed 声明的 enclosure——解析器不产出其他路径。"""
    from spiders.education_podcast.spider import _parse_feed
    parsed = _parse_feed(RSS_TMPL.format(items=ITEM_TMPL.format(n=1, min=1)).encode())
    assert parsed["items"][0]["enclosure_url"] == "https://example.com/audio/ep1.mp3"
    # 无 enclosure 的 item → enclosure_url=None → poller 标 failed（link_only），不会去猜别的路径
    parsed2 = _parse_feed(b"<rss><channel><item><title>x</title><guid>x</guid></item></channel></rss>")
    assert parsed2["items"][0]["enclosure_url"] is None
