"""Periodic session-jar probes for the identity pool (stale-session probe).

A stored session jar is a snapshot: the source can invalidate it at any time,
and nothing in the crawl path notices until the next run burns a whole lease
on a dead session (identity 27 sat 'active' for 7+ hours after its session
had died, 2026-10-09). The dispatcher therefore probes active identities
against the source's cheap endpoint through the identity's bound egress; a
probe that proves the session dead flips the identity to login_required so
the login station picks it up on its next launch.

Probe classification mirrors the auth broker's classifyProbeResponse
(fd-law-data/lib/auth-broker/proxy.js DEFAULT_PROBES/classifyProbeResponse):
HTTP status first, then the source envelope's code, then ban/login keyword
markers. Only a proven-dead session (login_required/banned) flips the
identity; transient failures (source_error, rate_limited, ...) only stamp
last_probe_at so the pool is not hammered and the state machine is not
corrupted by a network blip.

Env: FD_PROBE_ENABLED (default 1; 0 disables the tick step),
     FD_PROBE_MAX_AGE_SECONDS (default 10800 = 3h),
     FD_PROBE_MAX_PER_TICK (default 2),
     FD_PROBE_TIMEOUT_SECONDS (default 20).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import auth as _auth

# Source probe endpoints, mirroring the broker's DEFAULT_PROBES["rmfyalk-case-library"]
# (fd-law-data/lib/auth-broker/proxy.js:6-27). Keyed by the identity's `source`
# (the auth_profile name, e.g. 'rmfyalk'), not the crawl source name.
PROBES = {
    "rmfyalk": {
        "method": "POST",
        "url": "https://rmfyalk.court.gov.cn/cpws_al_api/api/cpwsAl/search",
        "headers": {"content-type": "application/json;charset=UTF-8",
                    "referer": "https://rmfyalk.court.gov.cn/view/list.html",
                    "origin": "https://rmfyalk.court.gov.cn"},
        "body": {"page": 1, "size": 1, "searchParams": {"userSearchType": 1,
                 "isAdvSearch": "0", "selectValue": "qw", "lib": "cpwsAl_qb",
                 "sort_field": ""}},
    },
}

_BAN_RE = re.compile(r"account.*ban|账号.*封禁|已对您的账号进行封禁")
_LOGIN_RE = re.compile(r"login|signin|登录|验证码|captcha|slider", re.IGNORECASE)


def classify_probe(status: int, text: str, payload) -> dict:
    """Verdict for one probe response: {"ok", "kind", "reason"}.

    The broker's exact ladder (proxy.js classifyProbeResponse): HTTP status
    first; a JSON source envelope then wins before keyword scanning (case
    content may legitimately contain 登录/验证码); ban markers before login
    markers; anything else is an unexpected payload.
    """
    body = str(text or "").lower()
    if status == 401:
        return {"ok": False, "kind": "login_required", "reason": "HTTP 401"}
    if status == 403:
        return {"ok": False, "kind": "forbidden", "reason": "HTTP 403"}
    if status == 429:
        return {"ok": False, "kind": "rate_limited", "reason": "HTTP 429"}
    if status >= 500:
        return {"ok": False, "kind": "source_error", "reason": f"HTTP {status}"}
    if status != 200:
        return {"ok": False, "kind": "unexpected_status", "reason": f"HTTP {status}"}
    # A valid source envelope wins before keyword scanning: case content may
    # legitimately contain words such as 登录 or 验证码. Any parsed JSON
    # container counts as an envelope (the broker's `typeof payload ===
    # "object"` is true for arrays too); scalars fall through to the scan.
    if isinstance(payload, (dict, list)):
        code = payload.get("code") if isinstance(payload, dict) else None
        if str(code) == "0":
            return {"ok": True, "kind": "ok", "reason": "source accepted session"}
        if str(code) == "401":
            return {"ok": False, "kind": "login_required",
                    "reason": "source code 401"}
        return {"ok": False, "kind": "unexpected_payload",
                "reason": f"source code {code}"}
    if _BAN_RE.search(body):
        return {"ok": False, "kind": "banned", "reason": "account ban marker"}
    if _LOGIN_RE.search(body):
        return {"ok": False, "kind": "login_required",
                "reason": "login or captcha marker"}
    return {"ok": False, "kind": "unexpected_payload",
            "reason": "probe response was not JSON"}


def _json_or_none(text: str):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a non-JSON body is a probe verdict, not an error
        return None


def _http_probe(definition: dict, jar: dict, proxy_url: str | None,
                timeout: float = 20) -> tuple[int, str, object]:
    """One probe request through the identity's egress; (status, text, payload).

    Headers mirror the broker: the jar's browser user-agent (default
    "Mozilla/5.0"), the definition's own headers, the jar's token header and
    the jar's cookie string. Network errors raise — the caller classifies
    them as source_error.
    """
    jar = jar or {}
    headers = {"user-agent": (jar.get("browser") or {}).get("userAgent")
               or "Mozilla/5.0"}
    headers.update(definition.get("headers") or {})
    auth_part = jar.get("auth") or {}
    if auth_part.get("headerName") and auth_part.get("userToken"):
        headers[auth_part["headerName"]] = auth_part["userToken"]
    cookies = "; ".join(
        f"{c['name']}={c['value']}"
        for c in ((jar.get("storageState") or {}).get("cookies") or [])
        if isinstance(c, dict) and c.get("name") and c.get("value"))
    if cookies:
        headers["cookie"] = cookies

    data = None
    if definition.get("body") is not None:
        data = json.dumps(definition["body"], ensure_ascii=False).encode("utf-8")
        headers.setdefault("content-type", "application/json")
    req = urllib.request.Request(definition["url"], data=data, headers=headers,
                                 method=definition.get("method", "GET"))
    opener = None
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    try:
        resp = (opener.open if opener else urllib.request.urlopen)(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        # A status IS a probe result (401/403/429/5xx carry the verdict); only
        # transport failures propagate.
        text = e.read().decode("utf-8", "replace")
        return e.code, text, _json_or_none(text)
    with resp:
        text = resp.read().decode("utf-8", "replace")
    return resp.status, text, _json_or_none(text)


def _event(cur, identity_id: int, kind: str, detail: str = "") -> None:
    """Identity event write: auth._event when present, same shape otherwise."""
    helper = getattr(_auth, "_event", None)
    if helper is not None:
        helper(cur, identity_id, kind, detail)
        return
    cur.execute(
        "INSERT INTO crawl_identity_events (identity_id, kind, detail, lease_token) "
        "VALUES (%s,%s,%s,%s)", (identity_id, kind, detail[:500], None))


def probe_stale_identities(conn, now=None, max_age_seconds=None,
                           max_per_tick=None, fetch=None) -> list[dict]:
    """Probe active identities whose last probe is older than the max age.

    Picks up to max_per_tick stale identities (NULL last_probe_at first, so a
    freshly flipped identity is checked before it is ever used) and probes
    each through its bound egress. A proven-dead session
    (login_required/banned) is reported via auth.report_auth_failed, which
    flips the identity; any other failure only stamps last_probe_at and
    records a 'note' event. Every identity is isolated: one failure never
    aborts the loop. Returns one dict per probed identity.
    """
    if os.environ.get("FD_PROBE_ENABLED", "1") == "0":
        return []
    now = now or datetime.now(timezone.utc)
    if max_age_seconds is None:
        max_age_seconds = int(os.environ.get("FD_PROBE_MAX_AGE_SECONDS", "10800"))
    if max_per_tick is None:
        max_per_tick = int(os.environ.get("FD_PROBE_MAX_PER_TICK", "2"))
    timeout = float(os.environ.get("FD_PROBE_TIMEOUT_SECONDS", "20"))

    with conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, source, account_alias, session_ref, egress_ref
               FROM crawl_identities
               WHERE status = 'active' AND session_ref IS NOT NULL
                 AND (last_probe_at IS NULL
                      OR last_probe_at < %s - make_interval(secs => %s))
               ORDER BY last_probe_at ASC NULLS FIRST
               LIMIT %s""",
            (now, max_age_seconds, max_per_tick))
        rows = cur.fetchall() or []

    out: list[dict] = []
    for ident_id, source, account_alias, session_ref, egress_ref in rows:
        definition = PROBES.get(source)
        if not definition:
            continue  # no cheap endpoint for this source: nothing to probe
        try:
            jar = _auth.fetch_jar(session_ref)
            egress = _auth.resolve_egress(conn, egress_ref)
            proxy_url = egress["proxy_url"] if egress else None
            try:
                status, text, payload = (fetch or _http_probe)(
                    definition, jar, proxy_url, timeout)
                verdict = classify_probe(status, text, payload)
            except Exception as e:  # noqa: BLE001 - transport failure is source_error
                verdict = {"ok": False, "kind": "source_error", "reason": str(e)}
            if verdict["ok"]:
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE crawl_identities SET last_probe_at=now(), "
                        "updated_at=now() WHERE id=%s", (ident_id,))
                    _event(cur, ident_id, "probe", "ok")
            elif verdict["kind"] in ("login_required", "banned"):
                # Proven dead: report_auth_failed flips the identity to
                # login_required (keyed by source+account_alias, not id).
                _auth.report_auth_failed(
                    conn, source, account_alias,
                    f"probe {verdict['kind']}: {verdict['reason']}"[:500])
            else:
                # Transient: do not flip; stamp the probe so we do not hammer.
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE crawl_identities SET last_probe_at=now(), "
                        "updated_at=now() WHERE id=%s", (ident_id,))
                    _event(cur, ident_id, "note",
                           f"probe {verdict['kind']}: {verdict['reason']}")
            out.append({"id": ident_id, "source": source,
                        "account_alias": account_alias,
                        "kind": verdict["kind"], "reason": verdict["reason"]})
        except Exception as e:  # noqa: BLE001 - one bad identity must not stop the tick
            print(f"fd-dispatcher: session probe failed for identity {ident_id} "
                  f"({source}/{account_alias}): {e}", file=sys.stderr)
    return out
