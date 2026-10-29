"""Extend the free PythonAnywhere web app ("Run until 1 month from today").

Credentials come from environment variables PA_USERNAME / PA_PASSWORD (optionally PA_HOST);
locally they may also be read from the git-ignored `.env`. Usage:
    python deploy/renew_pythonanywhere.py
"""

from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_env_file  # noqa: E402

TIMEOUT = 30
CSRF_FIELD = "csrfmiddlewaretoken"
EXPIRY_PATTERN = re.compile(
    r'class="[^"]*webapp_expiry[^"]*"[^>]*>.*?<strong>\s*([^<]+?)\s*</strong>', re.DOTALL
)


class RenewError(RuntimeError):
    pass


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "form":
            self._current = {"action": attributes.get("action", "")}
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None and attributes.get("name"):
            self._current[attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def parse_forms(html: str) -> list[dict[str, str]]:
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


def extend_forms(html: str) -> list[dict[str, str]]:
    return [
        form
        for form in parse_forms(html)
        if "/extend" in form["action"].lower() and CSRF_FIELD in form
    ]


def parse_expiries(html: str) -> list[str]:
    return EXPIRY_PATTERN.findall(html)


def login(session: requests.Session, base: str, username: str, password: str) -> None:
    login_url = f"{base}/login/"
    page = session.get(login_url, timeout=TIMEOUT)
    page.raise_for_status()
    form = next((f for f in parse_forms(page.text) if "auth-username" in f), None)
    if form is None or CSRF_FIELD not in form:
        raise RenewError("Login form not found: PythonAnywhere page layout changed")
    response = session.post(
        login_url,
        data={
            CSRF_FIELD: form[CSRF_FIELD],
            "login_view-current_step": "auth",
            "auth-username": username,
            "auth-password": password,
        },
        headers={"Referer": login_url},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    if any(f.get("login_view-current_step") == "token" for f in parse_forms(response.text)):
        raise RenewError("Two-factor authentication is enabled; automatic login is impossible")
    if "/login" in response.url or "log out" not in response.text.lower():
        raise RenewError("Login failed: check PA_USERNAME / PA_PASSWORD")


def renew(session: requests.Session, base: str, username: str) -> list[str]:
    webapps_url = f"{base}/user/{username}/webapps/"
    page = session.get(webapps_url, timeout=TIMEOUT)
    page.raise_for_status()
    forms = extend_forms(page.text)
    if not forms:
        print(f"No extend button shown; current expiry: {parse_expiries(page.text) or 'unknown'}")
        return parse_expiries(page.text)
    for form in forms:
        response = session.post(
            urljoin(base, form["action"]),
            data={CSRF_FIELD: form[CSRF_FIELD]},
            headers={"Referer": webapps_url},
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            raise RenewError(f"Extend failed: HTTP {response.status_code}")
    after = session.get(webapps_url, timeout=TIMEOUT)
    after.raise_for_status()
    expiries = parse_expiries(after.text)
    print(f"Extended {len(forms)} web app(s); expiry now: {expiries or 'unknown'}")
    return expiries


def main() -> None:
    load_env_file(ROOT / ".env")
    username = os.environ.get("PA_USERNAME", "").strip()
    password = os.environ.get("PA_PASSWORD", "")
    if not username or not password:
        raise RenewError("PA_USERNAME and PA_PASSWORD must be set")
    base = f"https://{os.environ.get('PA_HOST', '').strip() or 'www.pythonanywhere.com'}"
    with requests.Session() as session:
        login(session, base, username, password)
        renew(session, base, username)


if __name__ == "__main__":
    try:
        main()
    except (RenewError, requests.RequestException) as exc:
        sys.exit(f"Renew failed: {type(exc).__name__}: {exc}")
