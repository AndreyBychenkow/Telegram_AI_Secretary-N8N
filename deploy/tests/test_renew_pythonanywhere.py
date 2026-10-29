from __future__ import annotations

from deploy.renew_pythonanywhere import extend_forms, parse_expiries, parse_forms

WEBAPPS_PAGE = """
<div class="webapp_pane">
  <p class="webapp_expiry">Disabled on <strong>Sunday 29 November 2026</strong></p>
  <form action="/user/bob/webapps/bob.pythonanywhere.com/extend" method="post">
    <input type="hidden" name="csrfmiddlewaretoken" value="tok123">
    <input type="submit" value="Run until 1 month from today">
  </form>
  <form action="/user/bob/webapps/bob.pythonanywhere.com/reload" method="post">
    <input type="hidden" name="csrfmiddlewaretoken" value="tok123">
  </form>
</div>
"""


def test_extend_forms_selects_only_extend_with_csrf() -> None:
    forms = extend_forms(WEBAPPS_PAGE)

    assert forms == [
        {
            "action": "/user/bob/webapps/bob.pythonanywhere.com/extend",
            "csrfmiddlewaretoken": "tok123",
        }
    ]


def test_extend_form_without_csrf_is_ignored() -> None:
    assert extend_forms('<form action="/x/extend"><input name="other" value="1"></form>') == []


def test_parse_expiries() -> None:
    assert parse_expiries(WEBAPPS_PAGE) == ["Sunday 29 November 2026"]
    assert parse_expiries("<p>nothing</p>") == []


def test_parse_login_form() -> None:
    html = (
        '<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="c">'
        '<input name="auth-username"><input type="password" name="auth-password"></form>'
    )

    assert parse_forms(html) == [
        {"action": "", "csrfmiddlewaretoken": "c", "auth-username": "", "auth-password": ""}
    ]
