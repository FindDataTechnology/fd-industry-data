"""sec-edgar-facts — SEC EDGAR XBRL company facts + submissions event face.

Source (all key-free, official SEC endpoints):

- Company facts (XBRL): ``GET https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json``
  -> ``facts["us-gaap"][tag]["units"][unit] = [{start, end, val, accn, fy, fp,
  form, filed, frame}, ...]``. Company facts carry no labels; the ``label``
  column is a curated us-gaap display label (see ``TAG_LABELS``), never a
  value invented per filer.
- Submissions / event face: ``GET https://data.sec.gov/submissions/CIK##########.json``
  -> ``filings.recent`` parallel arrays (form / accessionNumber / filingDate /
  reportDate / primaryDocument).
- CIK map: ``GET https://www.sec.gov/files/company_tickers.json`` (ticker -> cik_str).

SEC contract and measured behaviour (2026-11-07, direct from this host, no proxy):

- A ``User-Agent`` carrying an application name + contact email is REQUIRED:
  without it data.sec.gov answers **403** (measured). ``USER_AGENT`` below is
  the estate identifier used for this lane.
- SEC allows <=10 req/s. This unit is serial with ``REQUEST_PAUSE_SECONDS``
  (0.15 s) between requests; a full 12-company pass is 37 requests.
- ``Accept-Encoding: gzip`` is used and decoded locally: the same endpoint was
  ~256 KB gzip vs ~3.6 MB plain for MCD alone (measured).
- Direct connection works; no egress-pool proxy is involved.

XBRL whitelist (``TAG_WHITELIST``) — exact tag names, no fuzzy matching:

    Revenues
    RevenueFromContractWithCustomerExcludingAssessedTax
    RevenueFromContractWithCustomerIncludingAssessedTax  (payload only — a
                         different caliber: it includes tax collected on
                         behalf of authorities, so it is never stitched into
                         the same series as the Excluding tag)
    SalesRevenueNet
    OperatingIncomeLoss
    NetIncomeLoss
    RestaurantRevenue   (only if a filer actually discloses it — none of the
                         17 currently do)

A fact is kept only when its ``form`` is 10-Q or 10-K **and** its ``frame``
matches ``^CY\\d{4}Q[1-4]$``. Consequences, kept deliberately visible:

- Annual facts (``frame`` = ``CY2024``) and non-10-Q/10-K facts are dropped.
- A December fiscal-year filer's fiscal Q4 exists only inside the 10-K, and
  the 10-K tags it as an *annual* duration, not as a quarterly frame — so no
  Q4 row is emitted for those filers. This is a known coverage hole of the
  frame-filtered surface, documented in README and the per-filer tag map.
- Frames are **calendar** quarters, not issuer fiscal quarters: e.g. Darden's
  fiscal Q2 (ending 2025-11-23) carries ``frame=CY2025Q4``. ``fy``/``fp``
  stay the filer's own fiscal labels and are never rewritten.

Restatement handling: the same (tag, unit, start, end) can appear in several
filings (later comparatives / amended figures). The latest ``filed`` entry
wins (deterministic tie-break on accession), never an arithmetic merge.

Failure contract: ticker-map fetch, CIK resolution, any companyfacts or
submissions fetch, non-JSON / non-object payloads, or a missing ``us-gaap``
facts block raise ``RuntimeError`` — a red run, never an empty "success".
A whitelist tag absent for a filer is data reality, logged and recorded in
``docs/sec-edgar-tag-map.md``, not a failure; a run that yields zero rows is.

Boundary note (MUST accompany this lane):

    本车道属性：金融车道扩展。**2026-10-07 侦察簿边界注记**：裁决理由 = 防重叠；
    美股（SEC EDGAR）在平台此前**无既有财报车道**，本扩展不构成与既有财报线的重叠。
    本单元只取 XBRL 事实与 filings 元数据（事件面），不解析 10-Q/10-K 正文全文。
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache

from fd_industry_data import universes

logger = logging.getLogger("sec-edgar-facts")

SOURCE = "sec-edgar-facts"

# SEC requires "app name + contact email"; without it data.sec.gov returns 403.
USER_AGENT = "RestaurantVertical research@finddatatech.cloud"

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_DOC_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{doc}"
)

REQUEST_PAUSE_SECONDS = 0.15   # SEC limit is <=10 req/s; serial + 0.15s is far below
REQUEST_TIMEOUT_SECONDS = 90

# 默认监控宇宙来自注册表 universes/us.yaml（vertical-lane-generalization）。
# 本单元 MUST NOT 硬编码公司列表：新增垂直线只登记注册表，不改这里。
DEFAULT_REGION = "US"


@lru_cache(maxsize=1)
def _default_tickers() -> tuple[str, ...]:
    """注册表驱动的默认宇宙（顺序即注册表顺序）。注册表不可用即硬失败。"""

    return universes.region_tickers(DEFAULT_REGION)


DEFAULT_FORMS = ("10-Q", "10-K", "8-K")
FACT_FORMS = frozenset({"10-Q", "10-K"})

# Exact us-gaap tag whitelist — no prefix/fuzzy matching. Order is the row
# order for a given period (revenue tags first, bottom line last).
TAG_WHITELIST = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    # Payload-only: HYFM switched to this tag in 2026 and the caliber includes
    # assessed tax, so the extractors keep it as evidence but never append it
    # to the Excluding series (a silent stitch would fake a revenue jump).
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "RestaurantRevenue",
)

# us-gaap standard element labels (curated locally: companyfacts carries no
# labels). These are display labels for the exact tags above, not per-filer
# interpretations.
TAG_LABELS = {
    "Revenues": "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax": (
        "Revenue from Contract with Customer, Excluding Assessed Tax"
    ),
    "RevenueFromContractWithCustomerIncludingAssessedTax": (
        "Revenue from Contract with Customer, Including Assessed Tax"
    ),
    "SalesRevenueNet": "Sales Revenue, Net",
    "OperatingIncomeLoss": "Operating Income (Loss)",
    "NetIncomeLoss": "Net Income (Loss)",
    "RestaurantRevenue": "Restaurant Revenue",
}

QUARTER_FRAME_RE = re.compile(r"^CY\d{4}Q[1-4]$")


def _get_json(url: str, what: str) -> dict:
    """GET + gzip decode + JSON parse; any failure raises RuntimeError (red run).

    One retry is allowed for transient transport failures (measured: a single
    socket read can stall past the timeout on this host while the next attempt
    completes in ~3 s). HTTP status errors — including the 403 caused by a
    missing contact identity — are never retried.
    """
    req = urllib.request.Request(
        url,
        headers={
            # SEC requires the contact identity; omitting it yields 403.
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    raw: bytes | None = None
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
                raw = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
            break
        except urllib.error.HTTPError as e:
            hint = ""
            if e.code == 403:
                hint = (" — SEC rejected the request; check that User-Agent carries "
                        "an app name + contact email")
            raise RuntimeError(
                f"{SOURCE}: HTTP {e.code} for {what} ({url}){hint}"
            ) from e
        except Exception as e:  # noqa: BLE001 — transport failure; one retry
            last_error = e
            logger.warning("fetch %s attempt %d failed: %s", what, attempt, e)
            if attempt == 1:
                time.sleep(1.0)
    if raw is None:
        raise RuntimeError(
            f"{SOURCE}: cannot fetch {what} ({url}): {last_error}"
        ) from last_error

    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"{SOURCE}: invalid JSON from {what} ({url}): {e}"
        ) from e
    if not isinstance(doc, dict):
        raise RuntimeError(
            f"{SOURCE}: {what} payload is not a JSON object ({url})"
        )
    return doc


def _load_cik_map() -> dict[str, str]:
    """ticker -> zero-padded 10-digit CIK from SEC company_tickers.json."""
    doc = _get_json(TICKER_MAP_URL, "CIK ticker map")
    mapping: dict[str, str] = {}
    for value in doc.values():
        if not isinstance(value, dict):
            continue
        ticker = value.get("ticker")
        cik = value.get("cik_str")
        if not ticker or cik is None:
            continue
        try:
            mapping[str(ticker).strip().upper()] = f"{int(cik):010d}"
        except (TypeError, ValueError):
            continue
    if not mapping:
        raise RuntimeError(
            f"{SOURCE}: CIK ticker map {TICKER_MAP_URL} has no ticker entries"
        )
    return mapping


def _coerce_tickers(tickers) -> tuple[str, ...]:
    """Accept a tuple/list/JSON list or a comma-separated string; default universe."""
    if tickers is None:
        return _default_tickers()
    if isinstance(tickers, str):
        parts = [p.strip() for p in tickers.split(",")]
    elif isinstance(tickers, (list, tuple, set)):
        parts = [str(p).strip() for p in tickers]
    else:
        raise ValueError(
            f"{SOURCE}: tickers must be a list/tuple or comma-separated string, "
            f"got {type(tickers).__name__}"
        )
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        t = p.upper()
        if t not in seen:
            seen.add(t)
            out.append(t)
    if not out:
        raise ValueError(f"{SOURCE}: tickers resolved to an empty list")
    return tuple(out)


def _coerce_forms(forms) -> tuple[str, ...]:
    """Accept a tuple/list/JSON list or a comma-separated string of SEC form types."""
    if forms is None:
        return DEFAULT_FORMS
    if isinstance(forms, str):
        parts = [p.strip() for p in forms.split(",")]
    elif isinstance(forms, (list, tuple, set)):
        parts = [str(p).strip() for p in forms]
    else:
        raise ValueError(
            f"{SOURCE}: forms must be a list/tuple or comma-separated string, "
            f"got {type(forms).__name__}"
        )
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        f = p.upper()
        if f not in seen:
            seen.add(f)
            out.append(f)
    if not out:
        raise ValueError(f"{SOURCE}: forms resolved to an empty list")
    return tuple(out)


def _fact_rows(
    facts_doc: dict,
    ticker: str,
    cik10: str,
    quarters_back: int,
    scraped_at: str,
) -> tuple[list[dict], dict[str, dict]]:
    """Whitelisted quarterly facts for one filer -> (rows, per-tag availability).

    Availability is returned so callers (and the README tag map) can record
    which tags a filer actually reports; absence is data, never invented.
    """
    facts = facts_doc.get("facts")
    if not isinstance(facts, dict):
        raise RuntimeError(f"{SOURCE}: companyfacts for {ticker} has no 'facts' block")
    us_gaap = facts.get("us-gaap")
    if not isinstance(us_gaap, dict):
        raise RuntimeError(f"{SOURCE}: companyfacts for {ticker} has no 'us-gaap' facts")

    availability: dict[str, dict] = {}
    per_tag: dict[str, list[dict]] = {}

    for tag in TAG_WHITELIST:
        tag_node = us_gaap.get(tag)
        if not isinstance(tag_node, dict):
            availability[tag] = {"present": False, "first": None, "rows": 0}
            continue
        units = tag_node.get("units")
        if not isinstance(units, dict):
            availability[tag] = {"present": False, "first": None, "rows": 0}
            continue

        entries: list[dict] = []
        for unit, items in units.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("form") not in FACT_FORMS:
                    continue
                frame = str(item.get("frame") or "")
                if not QUARTER_FRAME_RE.match(frame):
                    continue
                start, end, val = item.get("start"), item.get("end"), item.get("val")
                if not start or not end or val is None:
                    continue
                entries.append(
                    {
                        "tag": tag,
                        "unit": unit,
                        "start": start,
                        "end": end,
                        "value": val,
                        "fy": item.get("fy"),
                        "fp": item.get("fp"),
                        "form": item.get("form"),
                        "filed": item.get("filed"),
                        "accn": item.get("accn"),
                    }
                )

        if not entries:
            availability[tag] = {"present": False, "first": None, "rows": 0}
            continue
        entries.sort(key=lambda e: (e["end"], e["start"], e["unit"]))
        first = entries[0]
        availability[tag] = {
            "present": True,
            "first": {
                "fy": first["fy"],
                "fp": first["fp"],
                "start": first["start"],
                "end": first["end"],
                "form": first["form"],
                "filed": first["filed"],
            },
            "rows": len(entries),
        }
        per_tag[tag] = entries

    # Window: the latest ``quarters_back`` distinct period-end dates across all
    # whitelisted tags of THIS filer (fiscal-period ends, not calendar frames).
    all_ends = sorted({e["end"] for entries in per_tag.values() for e in entries},
                      reverse=True)
    selected_ends = set(all_ends[:quarters_back])

    rows: list[dict] = []
    for tag in TAG_WHITELIST:  # whitelist order is the row order within a period
        entries = per_tag.get(tag)
        if not entries:
            continue
        # Restatements: one row per (unit, start, end); latest filed wins.
        best: dict[tuple, dict] = {}
        for e in entries:
            if e["end"] not in selected_ends:
                continue
            key = (e["unit"], e["start"], e["end"])
            current = best.get(key)
            if current is None or (e["filed"], e["accn"] or "") > (
                current["filed"], current["accn"] or ""
            ):
                best[key] = e
        for e in sorted(best.values(),
                        key=lambda x: (x["end"], x["start"], x["unit"]),
                        reverse=True):
            rows.append(
                {
                    "kind": "fact",
                    "ticker": ticker,
                    "cik": cik10,
                    "form": e["form"],
                    "fy": e["fy"],
                    "fp": e["fp"],
                    "start": e["start"],
                    "end": e["end"],
                    "filed": e["filed"],
                    "tag": tag,
                    "label": TAG_LABELS[tag],
                    "unit": e["unit"],
                    "value": e["value"],
                    "source": SOURCE,
                    "scraped_at": scraped_at,
                }
            )
    return rows, availability


def _event_rows(
    submissions_doc: dict,
    ticker: str,
    cik10: str,
    forms: tuple[str, ...],
    cutoff_date: str | None,
    scraped_at: str,
) -> list[dict]:
    """10-Q/10-K/8-K filing events from submissions.filings.recent."""
    filings = submissions_doc.get("filings")
    if not isinstance(filings, dict):
        raise RuntimeError(f"{SOURCE}: submissions for {ticker} has no 'filings' block")
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        raise RuntimeError(
            f"{SOURCE}: submissions for {ticker} has no 'filings.recent' block"
        )

    columns = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument")
    arrays = {c: recent.get(c) for c in columns}
    for c, arr in arrays.items():
        if not isinstance(arr, list):
            raise RuntimeError(
                f"{SOURCE}: submissions for {ticker} lacks filings.recent.{c}"
            )
    lengths = {len(arr) for arr in arrays.values()}
    if len(lengths) != 1:
        raise RuntimeError(
            f"{SOURCE}: submissions for {ticker} has ragged filings.recent arrays "
            f"({sorted(lengths)})"
        )

    cik_int = int(cik10)
    rows: list[dict] = []
    for i in range(lengths.pop()):
        form = arrays["form"][i]
        if form not in forms:
            continue
        filed = arrays["filingDate"][i] or None
        if cutoff_date and filed and filed < cutoff_date:
            continue
        accession = arrays["accessionNumber"][i] or None
        doc = arrays["primaryDocument"][i] or None
        url = None
        if accession and doc:
            url = ARCHIVE_DOC_URL.format(
                cik_int=cik_int, accession_nodash=accession.replace("-", ""), doc=doc
            )
        rows.append(
            {
                "kind": "event",
                "ticker": ticker,
                "cik": cik10,
                "form": form,
                "accession": accession,
                "filed": filed,
                "report_date": arrays["reportDate"][i] or None,
                "primary_doc_url": url,
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
        )
    return rows


def run_sec_edgar_facts(
    limit: int = 100,
    tickers=None,
    forms=DEFAULT_FORMS,
    quarters_back: int = 8,
) -> list[dict]:
    """Entry point for fd-runner (spiders/sec-edgar-facts).

    Args:
        limit: maximum rows to return (facts and events share the budget;
            rows are emitted per ticker: its facts, then its events).
        tickers: company universe (JSON list or comma-separated string);
            default = the full US registry (``universes/us.yaml``).
        forms: SEC form types kept on the event face (default 10-Q/10-K/8-K).
            The fact face is always restricted to 10-Q/10-K.
        quarters_back: rolling window of distinct fiscal period ends used for
            facts, and the filing lookback cutoff for events.

    Returns:
        Row dicts per manifest columns: ``kind`` is ``fact`` or ``event``.
        Raises RuntimeError on any fetch/parse/CIK failure (red run); a run
        that produces zero rows is also an error, never an empty success.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"{SOURCE}: limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    try:
        quarters_back = int(quarters_back)
    except (TypeError, ValueError):
        raise ValueError(
            f"{SOURCE}: quarters_back must be an int, got {quarters_back!r}"
        ) from None
    if quarters_back <= 0:
        raise ValueError(f"{SOURCE}: quarters_back must be >= 1, got {quarters_back}")

    ticker_list = _coerce_tickers(tickers)
    form_list = _coerce_forms(forms)

    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package

        def cancel_set() -> bool:
            return False

    scraped_at = datetime.now(timezone.utc).isoformat()
    cik_map = _load_cik_map()

    rows: list[dict] = []
    missing_tags: dict[str, list[str]] = {}

    for index, ticker in enumerate(ticker_list):
        if cancel_set():
            logger.info("cancel requested; stopping after %d ticks", index)
            break
        cik10 = cik_map.get(ticker)
        if not cik10:
            raise RuntimeError(
                f"{SOURCE}: ticker {ticker} not resolvable in {TICKER_MAP_URL}"
            )

        facts_doc = _get_json(
            COMPANY_FACTS_URL.format(cik10=cik10), f"companyfacts for {ticker}"
        )
        time.sleep(REQUEST_PAUSE_SECONDS)
        submissions_doc = _get_json(
            SUBMISSIONS_URL.format(cik10=cik10), f"submissions for {ticker}"
        )
        time.sleep(REQUEST_PAUSE_SECONDS)

        fact_rows, availability = _fact_rows(
            facts_doc, ticker, cik10, quarters_back, scraped_at
        )
        absent = [tag for tag, meta in availability.items() if not meta["present"]]
        if absent:
            missing_tags[ticker] = absent
            logger.info("%s: whitelist tags not reported: %s", ticker, ", ".join(absent))
        if not fact_rows:
            logger.warning(
                "%s: no whitelisted 10-Q/10-K quarterly facts (tags present: %s)",
                ticker,
                ", ".join(t for t, m in availability.items() if m["present"]) or "none",
            )

        cutoff = min((r["end"] for r in fact_rows), default=None)
        event_rows = _event_rows(
            submissions_doc, ticker, cik10, form_list, cutoff, scraped_at
        )

        for row in fact_rows + event_rows:
            if len(rows) >= limit:
                break
            rows.append(row)
        if len(rows) >= limit:
            break

    if missing_tags:
        logger.info("sec-edgar-facts: per-ticker missing whitelist tags: %s", missing_tags)
    if not rows:
        raise RuntimeError(
            f"{SOURCE}: zero rows for {list(ticker_list)} with forms={list(form_list)} "
            f"quarters_back={quarters_back} — refusing to report an empty success"
        )
    logger.info(
        "sec-edgar-facts: %d rows (%d facts / %d events) for %d ticker(s)",
        len(rows),
        sum(1 for r in rows if r["kind"] == "fact"),
        sum(1 for r in rows if r["kind"] == "event"),
        len({r["ticker"] for r in rows}),
    )
    return rows[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for row in run_sec_edgar_facts(limit=20):
        print(row)
