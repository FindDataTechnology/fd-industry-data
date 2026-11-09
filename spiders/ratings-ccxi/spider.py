"""中诚信国际 银行跟踪评级（dqgz / cspj / bdqgz 三接口，HMAC-SHA256 签名）。

站点是 Vue SPA（``www.ccxi.com.cn``），数据接口在 ``website-api.ccxi.com.cn``，
每个请求都要带四个签名头。签名算法从首页主包 ``/js/app.835690d9.js`` 实读还原
（详见 README「签名算法还原」一节），要点：

```
ts      = Date.now()                      # 毫秒。字符串
nonce   = 16 位十六进制（8 随机字节）
url     = 请求路径 + "?" + 按插入顺序序列化的 params
body    = ""                              # GET 无 body
msg     = METHOD + "\n" + url + "\n" + body + "\n" + ts + "\n" + nonce
sign    = HMAC-SHA256(key, msg).hexdigest()
headers = X-Api-Version / X-Api-Timestamp / X-Api-Nonce / X-Api-Sign
```

密钥（前端常量 ``H``）与版本（``I = "v1"``）**均可配置**：
``CCXI_HMAC_KEY`` / ``CCXI_API_VERSION``；未设置时回落到实测内置值，
设置了空值则直接报错（不静默降级）。

三个接口（全部 GET、免登录，``likes`` 为服务端模糊匹配）：

| kind  | 路径 | 名称字段（银行过滤） | orderby |
|---|---|---|---|
| dqgz  | ``/admin/content/dqgz/page?codetranslate=true``  | ``tracename``  | ``"rankdate"``（默认降序） |
| cspj  | ``/admin/content/cspj/page?codetranslate=true``  | ``bondname``   | ``{"rankdate": "desc"}`` |
| bdqgz | ``/admin/content/bdqgz/page``                    | ``reportname`` | ``{"rankdate": "desc"}`` |

同一份报告会按"所覆盖的每只债"各出一条记录（实测 2022 年贵阳农商行二级资本债
两条记录指向同一个 PDF），故按 ``report_url`` 去重，并把被合并的债券简称与条数
保留在 ``bond_abbreviations`` / ``bond_count`` 列。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

__all__ = ["run_ratings_ccxi"]

BASE_URL = "https://website-api.ccxi.com.cn"

#: 前端 app.js 常量 ``I``（X-Api-Version）。
DEFAULT_API_VERSION = "v1"
#: 前端 app.js 常量 ``H``（HMAC key）。仅作为"未配置环境变量时"的回落值；
#: 上游换钥后可用 ``CCXI_HMAC_KEY`` 覆盖，无需改代码。
#: 该键是公开前端包里的常量，不是用户私密凭据。
DEFAULT_HMAC_KEY = "df464cdfcf2974a621abaeda20a2d1d4c7d3bb969f61aaee11d4fbe78fd859c2"

ENV_KEY = "CCXI_HMAC_KEY"
ENV_VERSION = "CCXI_API_VERSION"
ENV_BASE = "CCXI_BASE_URL"

#: 每页条数（实测 100 可用，上游无 20 上限）。
PAGE_SIZE = 100
#: 请求间隔（网络礼节 >= 1s）。
SLEEP = 1.0
#: 单接口最多翻页数（防上游分页异常导致死循环）。
MAX_PAGES = 40

#: kind -> 接口定义。``filters`` 只保留"启用"状态（下架/停用不落库），
#: 银行范围由 ``likes`` 的名称模糊匹配决定（见 README「银行过滤规则」）。
ENDPOINTS: dict[str, dict] = {
    "dqgz": {
        "path": "/admin/content/dqgz/page?codetranslate=true",
        "label": "定期跟踪",
        "name_field": "tracename",
        "orderby": "rankdate",          # 前端传的是字符串，默认降序
        "filters": {"launchedstatus": "启用"},
    },
    "cspj": {
        "path": "/admin/content/cspj/page?codetranslate=true",
        "label": "初始评级",
        "name_field": "bondname",
        "orderby": {"rankdate": "desc"},
        "filters": {"launchedstatus": "启用"},
    },
    "bdqgz": {
        "path": "/admin/content/bdqgz/page",
        "label": "不定期跟踪",
        "name_field": "reportname",
        "orderby": {"rankdate": "desc"},
        "filters": {"launchedstatus": "启用"},
    },
}

_TYPE_ORDER = ("dqgz", "cspj", "bdqgz")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _credentials() -> tuple[str, str, str]:
    """返回 (base_url, hmac_key, api_version)。

    Raises:
        RuntimeError: 环境变量被显式设置为空字符串——这通常意味着密钥注入失败，
            静默回落到内置值会掩盖配置错误，因此直接报错。
    """
    base = os.environ.get(ENV_BASE)
    base = (base if base is not None else BASE_URL).strip().rstrip("/") or BASE_URL

    raw_key = os.environ.get(ENV_KEY)
    if raw_key is None:
        key = DEFAULT_HMAC_KEY
        source = f"内置实测默认值（{ENV_KEY} 未设置）"
    else:
        key = raw_key.strip()
        if not key:
            raise RuntimeError(
                f"{ENV_KEY} 已设置但为空：请注入中诚信 X-Api-Sign 的 HMAC 密钥，"
                "或删除该环境变量以使用内置回落值"
            )
        source = f"环境变量 {ENV_KEY}"

    raw_version = os.environ.get(ENV_VERSION)
    if raw_version is None:
        version = DEFAULT_API_VERSION
    else:
        version = raw_version.strip()
        if not version:
            raise RuntimeError(
                f"{ENV_VERSION} 已设置但为空：请注入 X-Api-Version（如 v1），"
                "或删除该环境变量以使用内置默认值"
            )

    print(f"[ratings-ccxi] 签名配置：key=<{source}> version={version}", flush=True)
    return base, key, version


def _encode(value) -> str:
    """前端 ``F``：encodeURIComponent + axios 的六处还原（: $ , 空格 [ ]）。"""
    return (
        urllib.parse.quote(str(value), safe="")
        .replace("%3A", ":")
        .replace("%24", "$")
        .replace("%2C", ",")
        .replace("%20", "+")
        .replace("%5B", "[")
        .replace("%5D", "]")
    )


def _js_json(value) -> str:
    """等价 ``JSON.stringify``：紧凑分隔符、非 ASCII 不转义。"""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _query(params: dict) -> str:
    """前端 ``V``：按插入顺序拼 query，嵌套对象按 JSON.stringify 内联。"""
    parts: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                parts.append(f"{_encode(key)}[]={_encode(item)}")
        else:
            if isinstance(value, dict):
                value = _js_json(value)
            parts.append(f"{_encode(key)}={_encode(value)}")
    return "&".join(parts)


def _signed_request(
    base: str, key: str, version: str, path: str, params: dict, *, timeout: int = 30
) -> dict:
    """带签名的 GET，返回 JSON dict。

    Raises:
        RuntimeError: 401/403（密钥或版本失效）或响应非 JSON。
    """
    stamp = str(int(time.time() * 1000))
    nonce = secrets.token_hex(8)
    target = path
    query = _query(params)
    if query:
        target += ("&" if "?" in target else "?") + query

    message = "\n".join(["GET", target, "", stamp, nonce])
    sign = hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {
        "X-Api-Version": version,
        "X-Api-Timestamp": stamp,
        "X-Api-Nonce": nonce,
        "X-Api-Sign": sign,
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.ccxi.com.cn",
        "Referer": "https://www.ccxi.com.cn/",
    }

    last_error: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(base + target, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                body = res.read()
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode("utf-8", "replace")
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"中诚信签名被拒（HTTP {exc.code}）：请更新 {ENV_KEY}/{ENV_VERSION}"
                    f"（密钥换版即失效）——{detail}"
                ) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"中诚信请求失败（{path}）：{last_error}")

    try:
        return json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"中诚信响应非 JSON（{path}）：{body[:120]!r}") from exc


def _row(kind: str, cfg: dict, rec: dict, stamp: str) -> dict:
    issuer = str(rec.get("issuers") or "").strip()
    name = str(rec.get(cfg["name_field"]) or "").strip()
    report_url = str(rec.get("ratingreport") or rec.get("bondreport") or "").strip()
    classification = "/".join(
        str(rec.get(k) or "").strip()
        for k in ("levelonech", "leveltwoch", "levelthreech")
        if str(rec.get(k) or "").strip()
    )
    abbreviation = str(rec.get("bondabbreviation") or "").strip()
    return {
        "report_type": kind,
        "report_type_cn": cfg["label"],
        "issuer": issuer,
        "report_title": name,
        "subject_rating": str(rec.get("subjectlevel") or "").strip(),
        "debt_rating": str(rec.get("debtlevel") or "").strip(),
        "outlook": str(rec.get("expectation") or "").strip(),
        "rating_date": str(rec.get("rankdate") or "").strip(),
        "pdf_url": report_url,
        "subject_report_url": str(rec.get("subjectreport") or "").strip(),
        "info_id": rec.get("id"),
        "bond_abbreviations": abbreviation,
        "bond_count": 1,
        "classification": classification,
        "source": "ratings-ccxi",
        "scraped_at": stamp,
    }


def _merge(into: dict, row: dict) -> None:
    """同一 report_url 的多条债券记录合并：累加条数、收集债券简称、补空字段。"""
    into["bond_count"] += 1
    abbreviations = [a for a in into["bond_abbreviations"].split(";") if a]
    if row["bond_abbreviations"] and row["bond_abbreviations"] not in abbreviations:
        abbreviations.append(row["bond_abbreviations"])
    into["bond_abbreviations"] = ";".join(abbreviations)
    for field in ("issuer", "subject_rating", "debt_rating", "outlook", "subject_report_url"):
        if not into[field] and row[field]:
            into[field] = row[field]


def _collect(
    kind: str,
    keyword: str,
    base: str,
    key: str,
    version: str,
    records_limit: int,
    since: str,
    until: str,
    stamp: str,
) -> list[dict]:
    """抓一个接口，按 report_url 去重后返回至多 ``records_limit`` 行。"""
    cfg = ENDPOINTS[kind]
    rows: dict[str, dict] = {}
    page = 1
    while page <= MAX_PAGES and len(rows) < records_limit:
        params: dict = {"pageNo": page, "pageSize": PAGE_SIZE}
        params["orderby"] = cfg["orderby"]
        params["filters"] = dict(cfg["filters"])
        if keyword:
            params["likes"] = {cfg["name_field"]: keyword}

        payload = _signed_request(base, key, version, cfg["path"], params)
        data = payload.get("data") or {}
        records = data.get("records") or []
        if not records:
            break

        stopped = False
        for rec in records:
            rating_date = str(rec.get("rankdate") or "").strip()
            if since and rating_date and rating_date < since:
                stopped = True          # 按 rankdate 降序，后续只会更旧
                continue
            if until and rating_date and rating_date > until:
                continue
            row = _row(kind, cfg, rec, stamp)
            dedupe_key = row["pdf_url"] or f"{kind}:{row['info_id']}"
            if dedupe_key in rows:
                _merge(rows[dedupe_key], row)
            else:
                rows[dedupe_key] = row

        total = data.get("total")
        print(
            f"[ratings-ccxi] {kind} page{page}：{len(records)} 条 / 累计报告 "
            f"{len(rows)} / total={total}",
            flush=True,
        )
        if stopped or len(records) < PAGE_SIZE:
            break
        if isinstance(total, int) and page * PAGE_SIZE >= total:
            break
        page += 1
        time.sleep(SLEEP)
    return list(rows.values())[:records_limit]


def _quotas(requested: list[str], limit: int) -> dict[str, int]:
    """把总行数预算均分到各接口，余数给靠前的接口（保证三接口都有产出）。"""
    base = limit // len(requested)
    remainder = limit % len(requested)
    return {
        kind: base + (1 if index < remainder else 0)
        for index, kind in enumerate(requested)
    }


def run_ratings_ccxi(
    limit: int = 200,
    keyword: str = "银行",
    rating_types: str = "",
    since: str = "",
    until: str = "",
) -> list[dict]:
    """中诚信国际 银行评级结果（dqgz 定期跟踪 / cspj 初始评级 / bdqgz 不定期跟踪）。

    Args:
        limit: 返回行数上限（去重后）。预算按接口均分，保证三接口都出现在结果里。
        keyword: 银行过滤关键词，交给上游 ``likes`` 模糊匹配（dqgz 用 tracename、
            cspj 用 bondname、bdqgz 用 reportname）；空串 = 不过滤。
        rating_types: 逗号分隔的接口子集（``dqgz,cspj,bdqgz``）；空 = 三接口全取。
            用于单接口专项回补或 golden 定窗重放。
        since: 起始评级日期 ``YYYY-MM-DD``（含）；空 = 不设下限。按 rankdate
            降序翻页，遇到整页更旧即停，避免为历史窗口扫全表。
        until: 结束评级日期 ``YYYY-MM-DD``（含）；空 = 不设上限。``since+until``
            同时给定可钉死一个历史窗口，使 golden 锚不随新报告漂移。

    Returns:
        每份报告一行（同一 ``report_url`` 的多只债合并，条数记在 ``bond_count``）。

    Raises:
        ValueError: 参数非法（负 limit / 未知接口名 / 非法日期）。
        RuntimeError: 签名配置为空、签名被拒、或三接口全部失败。
    """
    if limit <= 0:
        return []
    requested = [t.strip() for t in (rating_types or "").split(",") if t.strip()]
    if not requested:
        requested = list(_TYPE_ORDER)
    unknown = [t for t in requested if t not in ENDPOINTS]
    if unknown:
        raise ValueError(f"未知 rating_types：{unknown}（可选 {list(_TYPE_ORDER)}）")
    for label, value in (("since", since), ("until", until)):
        if value:
            try:
                datetime.strptime(value.strip(), "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"{label} 须为 YYYY-MM-DD，收到 {value!r}") from exc
    since = since.strip()
    until = until.strip()

    base, key, version = _credentials()
    stamp = _now()
    quotas = _quotas(requested, int(limit))

    rows: list[dict] = []
    failures: list[str] = []
    for kind in requested:
        try:
            rows.extend(
                _collect(
                    kind, keyword.strip(), base, key, version,
                    quotas[kind], since, until, stamp,
                )
            )
        except RuntimeError as exc:
            if "签名被拒" in str(exc):
                raise                      # 密钥失效是全局故障，重试无意义
            failures.append(f"{kind}: {exc}")
            print(f"[ratings-ccxi] {kind} 失败：{exc}", flush=True)
        time.sleep(SLEEP)

    if not rows and failures:
        raise RuntimeError("ratings-ccxi: 全部接口失败 —— " + "; ".join(failures))

    rows.sort(key=lambda r: (r["rating_date"], r["report_type"], r["report_title"]), reverse=True)
    return rows[: int(limit)]
