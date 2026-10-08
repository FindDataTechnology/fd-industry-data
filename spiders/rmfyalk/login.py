"""rmfyalk login unit — human-assisted OAuth capture (port of
fd-law-data lib/auth-broker/logins/rmfyalk.js).

Flow: fetch getGdLoginUrl -> open the login page in a browser -> the
operator completes login (法信 OAuth) -> we capture the loginGd callback's
userToken plus the browser storageState into a jar. Crawls authenticate
with the `faxin-cpws-al-token` header.

The crawl adapter itself (search/content APIs) is a follow-up port; this
unit only establishes the platform-side session.
"""
from __future__ import annotations

import json
import time
import urllib.request

BASE_URL = "https://rmfyalk.court.gov.cn"
LOGIN_URL_ENDPOINT = f"{BASE_URL}/cpws_al_api/api/user/getGdLoginUrl"
LOGIN_CALLBACK = "/cpws_al_api/api/user/loginGd"
AUTH_HEADER = "faxin-cpws-al-token"
HUMAN_BUDGET_SECONDS = 10 * 60

automation = "assisted"


def _entry_url() -> str:
    req = urllib.request.Request(
        LOGIN_URL_ENDPOINT, data=b"{}",
        headers={"content-type": "application/json;charset=UTF-8",
                 "referer": f"{BASE_URL}/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if str(payload.get("code")) != "0" or not payload.get("data"):
        raise RuntimeError(f"getGdLoginUrl rejected: {payload}")
    return str(payload["data"]).replace("http:", "https:")


def login(account_alias: str, proxy: dict | None = None) -> dict:
    """Human-assisted OAuth capture.

    ``proxy`` is Playwright's ``{server, username, password}`` dict the login
    station passes in: Chromium ignores credentials embedded in an
    environment proxy URL, so the egress must be handed to the browser
    explicitly (net::ERR_INVALID_AUTH_CREDENTIALS otherwise).
    """
    from playwright.sync_api import sync_playwright

    login_url = _entry_url()
    print(f"rmfyalk-login: opened login page; complete the login in the "
          f"window (account label: {account_alias})")
    captured: dict = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, proxy=proxy)
        ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        page = ctx.newPage() if hasattr(ctx, "newPage") else ctx.new_page()

        def on_response(resp):
            if LOGIN_CALLBACK in resp.url and "userToken" not in captured:
                try:
                    body = resp.json()
                    if str(body.get("code")) == "0" and body.get("data", {}).get(
                            "alUser", {}).get("userToken"):
                        captured["userToken"] = body["data"]["alUser"]["userToken"]
                        captured["raw"] = body
                except Exception:  # noqa: BLE001 - non-JSON neighbours
                    pass

        page.on("response", on_response)
        page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
        deadline = time.time() + HUMAN_BUDGET_SECONDS
        while not captured and time.time() < deadline:
            page.wait_for_timeout(500)
        if not captured:
            browser.close()
            raise RuntimeError("rmfyalk-login: human login not completed in time")
        storage = ctx.storage_state()
        browser.close()

    return {
        "version": 1,
        "source": "rmfyalk",
        "account_id": account_alias,
        "mode": "session_jar",
        "auth": {"kind": "header-token", "headerName": AUTH_HEADER,
                 "userToken": captured["userToken"], "raw": captured["raw"]},
        "storageState": storage,
        "browser": {"locale": "zh-CN", "timezone": "Asia/Shanghai"},
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
