"""eastmoney-data 的正文取值与失败语义回归（2026-11-14 立）。

两个坑都是当日实况，且都表现为「绿着但没数据」：

1. **`response.text` 可能为空串**。scrapling 的 `Fetcher.get` 对
   `content-type: text/plain` 的 JSON 接口返回的 `text` 是 `TextHandler` 且长度 0，
   而 `body` 里是完整 JSON（实测 1716 字节）。旧代码 `json.loads(response.text)`
   必然抛 `Expecting value: line 1 column 1 (char 0)`。
2. **解析不出时不能写占位行**。旧实现写一条字段全空的记录让运行「可审计」，
   结果是 status=success、rows_written=2、内容全空 —— 该源 59 次成功、118 行
   `indicator_value` 全为空串，而 CLI 与闸都绿。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPIDER = Path(__file__).resolve().parents[1] / "spiders" / "eastmoney-data" / "spider.py"

PAYLOAD = {
    "version": "x",
    "result": {
        "pages": 12,
        "data": [
            {"REPORT_DATE": "2026-10-01 00:00:00", "NATIONAL_BASE": 100.7, "NATIONAL_SAME": 0.7},
        ],
    },
}


def _load():
    spec = importlib.util.spec_from_file_location("eastmoney_spider", SPIDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _TextHandlerLike:
    """模仿 scrapling 的 TextHandler：长度 0、但 body 属性里有原始字节。"""

    def __init__(self, body: bytes):
        self.body = body

    def __len__(self):
        return 0

    def __str__(self):
        return ""


class _Resp:
    def __init__(self, status=200, body=b"", text=""):
        self.status = status
        self.body = body
        self.text = text
        self.url = "https://datacenter-web.eastmoney.com/api/data/v1/get?x=1"


def test_response_text_prefers_body_bytes():
    mod = _load()
    body = json.dumps(PAYLOAD).encode()
    # .text 是空串（旧代码读它就会 json 失败）
    assert mod.response_text(_Resp(body=body, text="")) == body.decode()


def test_response_text_unwraps_text_handler():
    mod = _load()
    body = json.dumps(PAYLOAD).encode()
    assert json.loads(mod.response_text(_Resp(body=body, text=_TextHandlerLike(body)))) == PAYLOAD


def test_parse_payload_emits_real_rows():
    mod = _load()
    rows = mod.parse_payload(json.dumps(PAYLOAD), url="https://x/api?reportName=RPT_ECONOMY_CPI")
    assert rows, "大盘 CPI 载荷必须解析出记录"
    assert all(r["indicator_value"] for r in rows), "不得产出字段全空的占位行"
    assert {r["category"] for r in rows} == {"CPI"}


def test_all_urls_unparsable_raises_instead_of_empty_success():
    mod = _load()
    mod.Fetcher = type("F", (), {"get": staticmethod(lambda *a, **k: _Resp(body=b"<html>", text=""))})
    mod.REQUEST_DELAY = 0
    with pytest.raises(RuntimeError):
        mod._crawl_records(["https://x/api"], save_results=False)
