"""宇宙注册表加载器契约（vertical-lane-generalization）。

对应 openspec/changes/add-hotel-vertical 任务 3.1–3.3：
- 注册表是车道默认宇宙的唯一真源；
- 加载失败硬报，不静默回落；
- 三个 region 文件可加载，且餐饮宇宙逐字段与改造前的车道内置值一致（零回归）。
"""

from __future__ import annotations

import textwrap

import pytest

from fd_industry_data import universes as U

# 改造前车道源码内置的默认宇宙（改前快照，逐字冻结）。零回归以此为准。
PRE_CHANGE_HK = (
    ("06862", "海底捞"),
    ("09922", "九毛九"),
    ("00520", "呷哺呷哺"),
    ("01405", "达势股份"),
    ("09658", "特海国际"),
    ("09987", "百胜中国"),
    ("00341", "大家乐"),
    ("00052", "大快活"),
    ("02150", "奈雪的茶"),
    ("01458", "周黑鸭"),
)
PRE_CHANGE_US = (
    "MCD", "SBUX", "YUM", "CMG", "QSR", "DRI",
    "DPZ", "WEN", "TXRH", "BROS", "WING", "EAT",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    U.clear_cache()
    yield
    U.clear_cache()


def test_registry_root_is_repo_universes_dir():
    assert U.registry_root().name == "universes"
    assert U.registry_root().is_dir()


@pytest.mark.parametrize(
    "region,expected",
    [("HK", 10), ("US", 17), ("CN-A", 49)],
)
def test_region_loads_with_expected_size(region, expected):
    """区域文件总条数（含各垂直线）。

    2026-11-08 add-flower-vertical 波3 追加：US 12→17（FLWS/CENT/SMG/HYFM/EDBL）、
    CN-A 48→49（002200）。HK 无新增（hkexnews 搜 flower 全年 0 命中，空集结论）。
    """
    assert len(U.load_region(region)) == expected


@pytest.mark.parametrize(
    "region,vertical,expected",
    [
        ("CN-A", "restaurant", 6),
        ("CN-A", "bank", 42),
        ("CN-A", "flower", 1),
        ("US", "restaurant", 12),
        ("US", "flower", 5),
        ("HK", "restaurant", 10),
    ],
)
def test_vertical_sizes_without_duplicates(region, vertical, expected):
    """每条垂直线的条数与去重（垂直线过滤是投影，不复制实体）。"""

    tickers = U.tickers_for_vertical(region, vertical)
    assert len(tickers) == expected
    assert len(set(tickers)) == len(tickers)


def test_cn_a_verticals_are_disjoint():
    """CN-A 各垂直线互不重叠（花卉只 002200，与餐饮 6、银行 42 无交集）。"""

    flower = set(U.tickers_for_vertical("CN-A", "flower"))
    restaurant = set(U.tickers_for_vertical("CN-A", "restaurant"))
    bank = set(U.tickers_for_vertical("CN-A", "bank"))
    assert not (flower & restaurant)
    assert not (flower & bank)
    assert len(restaurant | bank | flower) == len(U.load_region("CN-A"))


def test_hk_default_pairs_match_pre_change_lane_snapshot():
    assert U.region_pairs("HK") == PRE_CHANGE_HK


def test_us_default_tickers_match_pre_change_lane_snapshot():
    """餐饮线的美股默认宇宙零回归。

    快照按**垂直线**比对：注册表新增花卉实体后 ``region_tickers("US")`` 已包含它们，
    但餐饮车道的默认抓取集合必须与改造前逐字一致。
    """

    assert U.tickers_for_vertical("US", "restaurant") == PRE_CHANGE_US


def test_every_entity_has_required_fields():
    for region in U.REGION_FILES:
        for entity in U.load_region(region):
            assert entity.ticker
            assert entity.name_zh
            assert entity.segment
            assert entity.verticals


def test_verticals_filter_is_a_projection_not_a_copy():
    """多归属实体只登记一条记录，垂直线过滤是投影。"""

    hk_all = U.region_tickers("HK")
    restaurant = U.tickers_for_vertical("HK", "restaurant")
    assert set(restaurant) <= set(hk_all)
    assert len(restaurant) == len(set(restaurant))


def test_unknown_region_is_a_hard_error():
    with pytest.raises(U.UniverseRegistryError):
        U.load_region("JP")


def test_missing_registry_file_is_a_hard_error(tmp_path, monkeypatch):
    monkeypatch.setenv(U.ENV_OVERRIDE, str(tmp_path))
    with pytest.raises(U.UniverseRegistryError):
        U.load_region("HK")


def test_malformed_entity_is_a_hard_error(tmp_path, monkeypatch):
    (tmp_path / "hk.yaml").write_text(
        textwrap.dedent(
            """
            region: HK
            entities:
              - ticker: "06862"
                name_zh: 海底捞
                segment: full-service
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(U.ENV_OVERRIDE, str(tmp_path))
    with pytest.raises(U.UniverseRegistryError, match="required"):
        U.load_region("HK")


def test_duplicate_ticker_is_a_hard_error(tmp_path, monkeypatch):
    (tmp_path / "hk.yaml").write_text(
        textwrap.dedent(
            """
            region: HK
            entities:
              - ticker: "06862"
                name_zh: 海底捞
                segment: full-service
                verticals: [restaurant]
              - ticker: "06862"
                name_zh: 海底捞
                segment: full-service
                verticals: [hotel]
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(U.ENV_OVERRIDE, str(tmp_path))
    with pytest.raises(U.UniverseRegistryError, match="duplicate"):
        U.load_region("HK")
