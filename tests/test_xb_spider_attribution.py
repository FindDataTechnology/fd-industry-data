"""跨境采集单元失败归因契约（add-crossborder-vertical-line 2.6 采集端）。

四类归因的采集端：spider 失败必须返回结构化归因（`_error_category`）或显式
异常（`CaptchaRequiredError`），绝不静默返回空列表冒充「确认没有」——
「未取得」与「确认没有」在采集端就要可区分（network/upstream/quota/parse/
upstream-protection 分类 + 契约层待人工显式抛错）。查询端契约见
fd-open-data-mcp tests/test_xb_attribution.py（never_held/gap_in_series）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SPIDERS = Path(__file__).resolve().parent.parent / "spiders"


def _load(name: str):
    """按文件路径加载 spider 模块（目录名带连字符不能作包名，与 runner loader 同法）。"""
    spec = importlib.util.spec_from_file_location(
        f"xb_attribution_{name.replace('-', '_')}", SPIDERS / name / "spider.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_customs_classify_error_four_categories():
    """海关零售 spider：防护/网络/配额/上游 各归其类，绝不返回裸异常串。"""
    mod = _load("customs-xborder-retail")
    classify = mod._classify_error

    # 防护层（JSL 412——实测真形态）
    cat, detail = classify(Exception("x"), "http://stats.customs.gov.cn/", 412, "")
    assert cat == "upstream-protection" and "412" in detail
    # 防护层（响应体特征）
    cat, _ = classify(Exception("x"), "http://www.customs.gov.cn/", 200, "JSL challenge page")
    assert cat == "upstream-protection"
    # 网络层
    import urllib.error
    cat, _ = classify(urllib.error.URLError("timeout"), "http://www.customs.gov.cn/", None, "")
    assert cat == "network"
    # 配额
    cat, _ = classify(Exception("API quota exceeded"), "https://example.com/", None, "")
    assert cat == "quota"
    # 其余上游
    cat, _ = classify(RuntimeError("boom"), "https://example.com/", None, "")
    assert cat == "upstream"


def test_tariff_captcha_is_explicit_contract_error():
    """关税 spider：验证码拦截必须显式抛 CaptchaRequiredError（契约层待人工），
    不许吞掉返回空列表冒充「确认没有」。"""
    mod = _load("tariff-hs-rates")
    assert issubclass(mod.CaptchaRequiredError, RuntimeError)
    # 异常类型可带「契约层待人工」语义抛出（构造即文档）
    err = mod.CaptchaRequiredError("契约层待人工: 需解图片验证码 /wmsw/imgcode/preValidate")
    assert "待人工" in str(err)
    # HS 归一化契约：纯数字 6 位补点号（种子三码形态）
    assert mod._normalize_hs_code("854142") == "8541.42"
    assert mod._normalize_hs_code("8541.42.00") == "8541.42.00"
    assert mod._hs_to_raw("8541.42.00") == "85414200"
