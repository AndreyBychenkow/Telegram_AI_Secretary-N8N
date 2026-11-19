"""Служебные операции с аккаунтом PythonAnywhere через GitHub Actions.

Режимы (первый аргумент или переменная MODE):
    diag    — разбор страницы аккаунта, диагностика смены пароля
    change  — смена пароля на значение из NEW_PA_PASSWORD с проверкой входом
    email   — отправка формы смены почты на адрес из NEW_PA_EMAIL

Значения полей в выводе вырезаются, адреса почты маскируются.
Переменные окружения: PA_USERNAME, PA_PASSWORD, NEW_PA_PASSWORD, NEW_PA_EMAIL, PA_HOST.
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
    form = next(
        (f for f in parse_forms(page.text) if any(x["name"] == "auth-username" for x in f["fields"])), None
    )
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


def find_form(html: str, required: tuple[str, ...], forbidden: tuple[str, ...] = ()) -> dict | None:
    for form in parse_forms(html):
        names = [f["name"] for f in form["fields"]]
        if all(name in names for name in required) and not any(name in names for name in forbidden):
            return form
    return None


def resolve_action(account_url: str, form: dict) -> str:
    raw = form["action"].strip()
    # action="." — «та же страница»; urljoin превратил бы его в родительский каталог
    return account_url if raw in ("", ".", "./") else urljoin(account_url, raw)


def contexts(html: str, needles: tuple[str, ...], width: int = 200, limit: int = 8) -> list[str]:
    text = scrub(re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I))
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = " ".join(plain.split())
    found: list[str] = []
    for needle in needles:
        for match in re.finditer(re.escape(needle), plain, re.IGNORECASE):
            fragment = plain[max(0, match.start() - width): match.start() + width]
            if fragment and fragment not in found:
                found.append(fragment)
            if len(found) >= limit:
                return found
    return found


def change_password(session, base, username, account_url, page, candidate) -> bool:
    form = find_form(page.text, ("old_password", "new_password1", "new_password2"))
    if form is None:
        print("ОШИБКА: форма смены пароля не найдена")
        return False
    action = resolve_action(account_url, form)
    data = {}
    for field in form["fields"]:
        name = field["name"]
        if name == CSRF:
            data[CSRF] = field["value"]
        elif name == "old_password":
            data[name] = os.environ.get("PA_PASSWORD", "")
        elif "pass" in name.lower():
            data[name] = candidate
        else:
            data[name] = field["value"]
    print("Смена пароля: action=%s, отправляем поля %s" % (action.replace(base, ""), sorted(data)))
    response = session.post(action, data=data, headers={"Referer": account_url}, timeout=TIMEOUT)
    print("Ответ: HTTP %s, адрес %s" % (response.status_code, response.url.replace(base, "")))
    with requests.Session() as check:
        check.headers["User-Agent"] = "pa-tools/2.0"
        if login(check, base, username, candidate):
            print("РЕЗУЛЬТАТ: пароль изменён и проверен входом")
            return True
    print("РЕЗУЛЬТАТ: вход новым паролем не удался")
    return False


def change_email(session, base, username, account_url, page, new_email) -> bool:
    form = find_form(page.text, ("email",), forbidden=("old_password",))
    if form is None:
        print("ОШИБКА: форма смены почты не найдена")
        return False
    action = resolve_action(account_url, form)
    data = {}
    for field in form["fields"]:
        name = field["name"]
        if name == CSRF:
            data[CSRF] = field["value"]
        elif name == "email":
            data[name] = new_email
        else:
            data[name] = field["value"]
    print("Смена почты: action=%s, поля %s" % (action.replace(base, ""), sorted(data)))
    print("Новый адрес (замаскирован): %s" % mask_email(new_email))
    response = session.post(action, data=data, headers={"Referer": account_url}, timeout=TIMEOUT)
    print("Ответ: HTTP %s, адрес %s" % (response.status_code, response.url.replace(base, "")))
    print("\n===== сообщения на странице после отправки =====")
    for fragment in contexts(
        response.text,
        ("confirm", "verif", "sent", "check your", "email", "success", "error", "invalid", "already"),
    ):
        print("  *", fragment)

    after = session.get(account_url, timeout=TIMEOUT)
    current = sorted({mask_email(e) for e in EMAIL_RE.findall(after.text)})
    print("\nПочта на странице аккаунта сейчас (замаскирована): %s" % ", ".join(current[:5]))
    print("Ожидаемый адрес: %s" % mask_email(new_email))
    return True


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "diag")).strip().lower()
    base = base_url()
    username = os.environ.get("PA_USERNAME", "").strip()
    password = os.environ.get("PA_PASSWORD", "")

    session = requests.Session()
    session.headers["User-Agent"] = "pa-tools/2.0"
    if not login(session, base, username, password):
        print("ОШИБКА: вход не удался")
        return 3
    print("Вход: успешно")

    account_url = base + "/user/%s/account/" % username
    page = session.get(account_url, timeout=TIMEOUT)
    print("Страница аккаунта: HTTP %s" % page.status_code)

    if mode == "status":
        emails = sorted({mask_email(e) for e in EMAIL_RE.findall(page.text)})
        print("Почта в аккаунте (замаскирована): %s" % ", ".join(emails[:5]))
        for form in parse_forms(page.text):
            names = [f["name"] for f in form["fields"]]
            if "email" in names:
                for field in form["fields"]:
                    if field["name"] == "email" and field.get("value"):
                        print("Поле email на странице: %s" % mask_email(field["value"]))
        return 0

    if mode == "diag":
        for form in parse_forms(page.text):
            print("  форма: action=%r поля=%s" % (form["action"], [f["name"] for f in form["fields"]]))
        return 0

    if mode == "change":
        candidates = [
            v.strip() for v in os.environ.get("NEW_PA_PASSWORD", "").replace("\r", "\n").split("\n")
            if v.strip() and 8 <= len(v.strip()) <= 64 and all(ord(c) < 128 for c in v.strip())
        ]
        if not candidates:
            print("ОШИБКА: нет подходящего значения в NEW_PA_PASSWORD")
            return 5
        return 0 if change_password(session, base, username, account_url, page, candidates[0]) else 1

    if mode == "email":
        new_email = os.environ.get("NEW_PA_EMAIL", "").strip()
        if "@" not in new_email:
            print("ОШИБКА: не задан корректный NEW_PA_EMAIL")
            return 6
        return 0 if change_email(session, base, username, account_url, page, new_email) else 1

    print("ОШИБКА: неизвестный режим " + mode)
    return 7


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, requests.RequestException) as exc:
        print("ИСКЛЮЧЕНИЕ: %s: %s" % (type(exc).__name__, scrub(str(exc))))
        sys.exit(9)
