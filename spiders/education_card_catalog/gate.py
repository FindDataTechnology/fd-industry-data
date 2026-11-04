"""教育垂域负面清单闸（spec cap1 R2 拒接层 + R7 卡层负面清单的唯一真源）。

两道闸：
1. ``assert_catalog_source_allowed(url)``：目录登记面——拒接层（影子图书馆）域名
   连元数据卡片都拒收。
2. ``assert_body_acquisition_allowed(channel, url)``：正文/媒体获取面——只允许
   三条合法道（rss_enclosure / oa_pdf / cc_course_md），且下载平台域（B站/YouTube/
   Coursera/edX/Khan/中国MOOC/学堂在线/豆瓣/IA 借阅书）一律拒绝——转录的输入
   必须是节目自己 RSS 声明的公开 enclosure，任何平台下载路径混轨即拒（R7）。

消费方：
- education_podcast 蜘蛛：episode 行生成时校验 enclosure host（混轨→failed）；
- 转录插槽 _transcribe 前置校验；
- CI 扫描测试（tests/test_education_negative_gate.py）：正则扫教育蜘蛛源码，
  断言任何 fetch/urlopen 调用参数里不出现拒接/下载平台域名字面量。

卡层域名与拒接层的清单同步维护在 docs/education/supply-manifest.md（负面清单节）。
"""
from __future__ import annotations

import urllib.parse

# 拒接层：影子图书馆——元数据都不收
REFUSED_HOSTS = frozenset({
    "libgen.is", "libgen.rs", "libgen.lc", "libgen.st",
    "annas-archive.org", "annas-archive.se",
})

# 正文/媒体下载禁止面：商业平台与视频托管（spec cap1 R7）
BANNED_BODY_HOSTS = frozenset({
    "coursera.org", "www.coursera.org",
    "edx.org", "www.edx.org",
    "khanacademy.org", "www.khanacademy.org",
    "icourse163.org", "www.icourse163.org",
    "xuetangx.com", "www.xuetangx.com",
    "bilibili.com", "www.bilibili.com", "m.bilibili.com",
    "youtube.com", "www.youtube.com", "youtu.be", "music.youtube.com",
    "douban.com", "book.douban.com",
    "archive.org", "www.archive.org",
    "openlibrary.org",
})

# 合法正文道（channel 枚举）
ALLOWED_BODY_CHANNELS = frozenset({"rss_enclosure", "oa_pdf", "cc_course_md"})


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def assert_catalog_source_allowed(url: str) -> None:
    """拒接层域名连卡片都拒收（spec cap1 R2 拒接层）。"""
    if _host(url) in REFUSED_HOSTS:
        raise ValueError(
            f"refused source (shadow library, spec cap1 R2): {url} — "
            "not catalogable even as metadata card")


def assert_body_acquisition_allowed(channel: str, url: str) -> None:
    """正文/媒体只走三条合法道；下载平台混轨即拒（spec cap1 R7）。"""
    if channel not in ALLOWED_BODY_CHANNELS:
        raise ValueError(
            f"body channel {channel!r} not allowed — only {sorted(ALLOWED_BODY_CHANNELS)}")
    h = _host(url)
    assert_catalog_source_allowed(url)
    if h in BANNED_BODY_HOSTS:
        raise ValueError(
            f"body acquisition banned on {h!r} (card-layer/platform download, "
            f"spec cap1 R7): {url}")


def is_banned_body_host(url: str) -> bool:
    """查询面/健康面用的非抛错版本。"""
    return _host(url) in BANNED_BODY_HOSTS
