"""Wave 4 卡层测试（spec cap1 R2 拒接层 + R7 负面清单）。

- 5.1 验证：以 Coursera《Machine Learning Specialization》真课走人工录入全流程，
  出卡含外链与授权状态、无正文。
- 5.2 负面用例：带正文的卡被拒；影子图书馆被拒；RSS 混进平台下载链接被标 failed；
  闸函数逐道校验；CI 扫描教育蜘蛛源码无平台下载调用。
"""
from __future__ import annotations

import re

import pytest

# ── 5.1 人工录入走查 ─────────────────────────────────────────────────────────

def test_card_curated_flow_real_coursera_course():
    from spiders.education_card_catalog.spider import run_card_curated_import
    result = run_card_curated_import([{
        "title": "Machine Learning Specialization",
        "platform": "Coursera",
        "org": "DeepLearning.AI / Stanford Online",
        "source_url": "https://www.coursera.org/specializations/machine-learning-introduction",
        "acquisition": "curated",
    }])
    assert result["rejected"] == []
    (row,) = result["rows"]
    assert row["source_url"].startswith("https://www.coursera.org/")  # 原站外链
    assert row["fulltext_acquired"] is False                          # 「本目录未取正文」明示
    assert row["body_state"] == "link_only"
    assert row["license_status"] == "platform-content"
    assert row["license_layer"] == "metadata_only"
    assert row["commercial_ok"] is False                              # 授权不明→商用拒
    for f in ("source_url", "source", "scraped_at", "license"):
        assert row.get(f)                                             # spec cap1 R4


def test_card_rejects_body_carrying():
    from spiders.education_card_catalog.spider import run_card_curated_import
    result = run_card_curated_import([{
        "title": "x", "platform": "Coursera",
        "source_url": "https://www.coursera.org/learn/x",
        "acquisition": "curated",
        "transcript": "逐字稿正文混入",  # 卡层没有正文道
    }])
    assert result["rows"] == []
    assert "body" in result["rejected"][0]["reason"]


def test_card_rejects_shadow_library_and_bad_channel():
    from spiders.education_card_catalog.spider import run_card_curated_import
    result = run_card_curated_import([
        {"title": "pirated book", "platform": "LibGen",
         "source_url": "https://libgen.is/book?id=1", "acquisition": "curated"},
        {"title": "crawled", "platform": "B站课堂",
         "source_url": "https://www.bilibili.com/cheese/play/ss1",
         "acquisition": "crawler"},  # D8：不存在爬虫通道
    ])
    assert result["rows"] == []
    reasons = " | ".join(r["reason"] for r in result["rejected"])
    assert "refused" in reasons          # 拒接层
    assert "curated|partner" in reasons  # 录入通道闭集


# ── 5.2 闸函数 ───────────────────────────────────────────────────────────────

def test_gate_body_channels():
    from spiders.education_card_catalog.gate import assert_body_acquisition_allowed
    # 合法三道
    assert_body_acquisition_allowed("rss_enclosure", "https://lexfridman.com/feed/ep1.mp3")
    assert_body_acquisition_allowed("oa_pdf", "https://arxiv.org/pdf/2601.00001")
    assert_body_acquisition_allowed("cc_course_md", "https://ocw.mit.edu/courses/x/sitemap.xml")
    # 平台下载混轨即拒
    with pytest.raises(ValueError):
        assert_body_acquisition_allowed("rss_enclosure", "https://www.bilibili.com/video/BV1.mp3")
    with pytest.raises(ValueError):
        assert_body_acquisition_allowed("youtube_download", "https://youtube.com/watch?v=1")
    with pytest.raises(ValueError):
        assert_body_acquisition_allowed("rss_enclosure", "https://annas-archive.org/aa.pdf")


def test_podcast_poller_banned_enclosure_marked_failed(tmp_path):
    """RSS 里混进平台下载链接 → 该行 failed 并点名混轨（spec cap1 R7 端到端）。"""
    from spiders.education_podcast.spider import run_podcast_poller
    feed = tmp_path / "mixed.xml"
    feed.write_text(
        '<rss><channel><title>M</title>'
        '<item><title>Good</title><guid>g1</guid>'
        '<enclosure url="https://legit.example.com/ep1.mp3" type="audio/mpeg"/></item>'
        '<item><title>Mixed-track</title><guid>g2</guid>'
        '<enclosure url="https://www.bilibili.com/video/BV9.mp4" type="audio/mpeg"/></item>'
        '</channel></rss>')
    rows = run_podcast_poller(feeds=[feed.as_uri()], state_file=str(tmp_path / "s.json"))
    eps = {r["episode_guid"]: r for r in rows if r["kind"] == "episode"}
    assert eps["g1"]["episode_status"] == "pending"
    assert eps["g2"]["episode_status"] == "failed"
    assert "banned_platform_download_path" in eps["g2"]["episode_note"]


# ── CI 扫描：教育蜘蛛源码无平台下载调用 ──────────────────────────────────────

_EDU_SPIDERS = sorted(
    (p for p in __import__("pathlib").Path(__file__).resolve().parent.parent.glob(
        "spiders/education_*/*.py") if p.name != "gate.py"),
    key=str)

_BANNED_HOST_RE = re.compile(
    r"(?:_fetch|urlopen|urllib\.request\.Request)\s*\([^)]{0,200}?"
    r"(?:coursera\.org|edx\.org|khanacademy\.org|icourse163\.org|xuetangx\.com|"
    r"bilibili\.com|youtube\.com|youtu\.be|douban\.com|libgen|annas-archive|"
    r"archive\.org|openlibrary\.org)",
    re.S | re.I)


@pytest.mark.parametrize("spider_file", _EDU_SPIDERS, ids=lambda p: p.name)
def test_no_platform_download_calls_in_education_spiders(spider_file):
    src = spider_file.read_text()
    m = _BANNED_HOST_RE.search(src)
    assert m is None, (
        f"{spider_file.name}: banned platform host appears in a fetch/urlopen call — "
        f"spec cap1 R7 negative list. Match: {m.group(0)[:80] if m else ''}")
