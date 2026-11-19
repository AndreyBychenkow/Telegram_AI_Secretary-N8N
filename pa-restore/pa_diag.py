"""Диагностика страницы аккаунта PythonAnywhere: почему не проходит смена пароля.

Режимы:
    diag    — вход, разбор форм, попытка смены и вывод сырого HTML вокруг формы
    change  — только смена пароля с проверкой входа

Значения полей в выводе вырезаются, адреса почты маскируются.
Переменные окружения: PA_USERNAME, PA_PASSWORD, NEW_PA_PASSWORD, PA_HOST (опционально).
"""

from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

TIMEOUT = 30
CSRF = "csrfmiddlewaretoken"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
VALUE_RE = re.compile(r'(<input[^>]*?value=")([^"]*)(")', re.IGNORECASE)


def base_url() -> str:
    host = os.environ.get("PA_HOST", "").strip() or "www.pythonanywhere.com"
    return host if "://" in host else "https://" + host


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return value[:1] + "***"
    return f"{local[:1]}***@{domain[:1]}***{domain[domain.rfind('.'):]}"


def scrub(text: str) -> str:
    text = EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
    return VALUE_RE.sub(lambda m: m.group(1) + "(скрыто)" + m.group(3), text)


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {k: v or "" for k, v in attrs}
        if tag == "form":
            self._current = {"action": attributes.get("action", ""), "fields": []}
            self.forms.append(self._current)
        elif tag in ("input", "textarea", "select", "button") and self._current is not None:
            name = attributes.get("name")
            if name:
                # Django различает несколько форм на странице по имени нажатой кнопки,
                # поэтому кнопки с name тоже нужно отправлять.
                default_type = "submit" if tag == "button" else "text"
                self._current["fields"].append(
                    {
                        "name": name,
                        "type": attributes.get("type", default_type).lower(),
                        "value": attributes.get("value", ""),
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def parse_forms(html: str) -> list[dict]:
    parser = FormParser()
    parser.feed(html)
    return parser.forms


def login(session: requests.Session, base: str, username: str, password: str) -> bool:
    url = base + "/login/"
    page = session.get(url, timeout=TIMEOUT)
    page.raise_for_status()
    form = next((f for f in parse_forms(page.text) if any(x["name"] == "auth-username" for x in f["fields"])), None)
    if form is None:
        raise RuntimeError("форма входа не найдена")
    data = {"login_view-current_step": "auth", "auth-username": username, "auth-password": password}
    for field in form["fields"]:
        if field["name"] == CSRF:
            data[CSRF] = field["value"]
    response = session.post(url, data=data, headers={"Referer": url}, timeout=TIMEOUT)
    response.raise_for_status()
    for parsed in parse_forms(response.text):
        for field in parsed["fields"]:
            if field["name"] == "login_view-current_step" and field["value"] == "token":
                raise RuntimeError("включена двухфакторная авторизация")
    return "/login" not in response.url and "log out" in response.text.lower()


def password_form(html: str) -> dict | None:
    for form in parse_forms(html):
        names = [f["name"] for f in form["fields"]]
        if sum("pass" in name.lower() for name in names) >= 2 and "old_password" in names:
            return form
    return None


def form_block(html: str, needle: str = 'name="old_password"') -> str:
    """Возвращает HTML того <form>, внутри которого есть нужное поле."""
    for match in re.finditer(r"<form\b", html, re.IGNORECASE):
        end = html.find("</form>", match.start())
        if end == -1:
            continue
        block = html[match.start(): end + 7]
        if needle in block:
            return scrub(block)
    return "(форма не найдена)"


def contexts(html: str, needles: tuple[str, ...], width: int = 220, limit: int = 6) -> list[str]:
    """Куски текста вокруг ключевых слов (для поиска сообщений об ошибке)."""
    text = scrub(re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I))
    found: list[str] = []
    for needle in needles:
        for match in re.finditer(re.escape(needle), text, re.IGNORECASE):
            fragment = " ".join(text[max(0, match.start() - width): match.start() + width].split())
            if fragment and fragment not in found:
                found.append(fragment)
            if len(found) >= limit:
                return found
    return found


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "diag")).strip()
    base = base_url()
    username = os.environ.get("PA_USERNAME", "").strip()
    old_password = os.environ.get("PA_PASSWORD", "")
    raw_new = os.environ.get("NEW_PA_PASSWORD", "")

    session = requests.Session()
    session.headers["User-Agent"] = "pa-diag/1.0"
    if not login(session, base, username, old_password):
        print("ОШИБКА: вход старым паролем не удался")
        return 3
    print("Вход старым паролем: успешно")

    account_url = base + "/user/%s/account/" % username
    page = session.get(account_url, timeout=TIMEOUT)
    form = password_form(page.text)
    if form is None:
        print("ОШИБКА: форма смены пароля не найдена")
        return 4
    print("Форма смены пароля: action=%r, поля=%s" % (form["action"], [f["name"] for f in form["fields"]]))

    candidates = [v.strip() for v in raw_new.replace("\r", "\n").split("\n")
                  if v.strip() and 8 <= len(v.strip()) <= 64 and all(ord(c) < 128 for c in v.strip())]
    if not candidates:
        print("ОШИБКА: в NEW_PA_PASSWORD нет подходящего значения")
        return 5
    candidate = candidates[0]
    print("Кандидат: длина %d" % len(candidate))

    action = account_url if form["action"].strip() in ("", ".", "./") else urljoin(account_url, form["action"])

    data = {}
    for field in form["fields"]:
        name = field["name"]
        if name == CSRF:
            data[CSRF] = field["value"]
        elif name == "old_password":
            data[name] = old_password
        elif "pass" in name.lower():
            data[name] = candidate
        else:
            data[name] = field["value"]

    response = session.post(action, data=data, headers={"Referer": account_url}, timeout=TIMEOUT)
    print("Ответ на смену пароля: HTTP %s, адрес %s" % (response.status_code, response.url.replace(base, "")))

    if mode == "diag":
        print("\n===== БЛОК ФОРМЫ в ОТВЕТЕ =====")
        print(form_block(response.text))
        print("\n===== СООБЩЕНИЯ ОБ ОШИБКАХ в ОТВЕТЕ =====")
        for fragment in contexts(
            response.text,
            ("errorlist", 'class="error', "alert", "invalid", "incorrect", "must ", "password", "changed"),
        ):
            print("  *", fragment)
        print("\n===== БЛОК ФОРМЫ на ИСХОДНОЙ странице (для сравнения) =====")
        print(form_block(page.text))

    with requests.Session() as check:
        check.headers["User-Agent"] = "pa-diag/1.0"
        if login(check, base, username, candidate):
            print("РЕЗУЛЬТАТ: пароль изменён и проверен входом")
            return 0
    print("РЕЗУЛЬТАТ: вход новым паролем не удался")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, requests.RequestException) as exc:
        print("ИСКЛЮЧЕНИЕ: %s: %s" % (type(exc).__name__, scrub(str(exc))))
        sys.exit(9)
