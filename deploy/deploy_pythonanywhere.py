"""Deploy the bot to a PythonAnywhere web app via its API and set the Telegram webhook.

Reads secrets from the local, git-ignored `.env` file. Usage:
    python deploy/deploy_pythonanywhere.py
"""

from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import parse_env_file  # noqa: E402
from bot.telegram_api import TelegramClient  # noqa: E402

ENV_FILE = ROOT / ".env"
RUNTIME_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "GROQ_API_KEY",
    "WEBHOOK_SECRET",
    "GROQ_CHAT_MODEL",
    "GROQ_WHISPER_MODEL",
    "TIMEZONE",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CALENDAR_ID",
)
REMOTE_SA_NAME = "secrets/google-service-account.json"
MIN_PYTHON = (3, 10)
TIMEOUT = 60


class DeployError(RuntimeError):
    pass


class PythonAnywhere:
    def __init__(self, username: str, token: str, host: str) -> None:
        self.username = username
        self._base = f"https://{host}/api/v0/user/{username}"
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Token {token}"

    def request(self, method: str, path: str, ok: tuple[int, ...], **kwargs: Any) -> Any:
        response = self._session.request(method, f"{self._base}{path}", timeout=TIMEOUT, **kwargs)
        if response.status_code not in ok:
            raise DeployError(
                f"{method} {path}: HTTP {response.status_code}: {response.text[:500]}"
            )
        return (
            response.json()
            if response.content and "json" in response.headers.get("Content-Type", "")
            else None
        )

    def upload(self, remote_path: str, content: bytes) -> None:
        self.request(
            "POST", f"/files/path{remote_path}", (200, 201), files={"content": ("f", content)}
        )

    def best_python(self) -> tuple[int, int]:
        data = self.request("GET", "/default_python3_version/", (200,))
        versions = [
            tuple(int(part) for part in v.split("."))
            for v in data.get("available_python3_versions", [])
        ]
        usable = [v for v in versions if v >= MIN_PYTHON]
        if not usable:
            raise DeployError(f"No Python >= {MIN_PYTHON} available: {versions}")
        major, minor = max(usable)[:2]
        return major, minor


def load_local_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise DeployError(f"{ENV_FILE} not found. Copy .env.example to .env and fill it in.")
    env = parse_env_file(ENV_FILE.read_text(encoding="utf-8"))
    if not env.get("WEBHOOK_SECRET"):
        env["WEBHOOK_SECRET"] = secrets.token_urlsafe(32)
        with ENV_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"\nWEBHOOK_SECRET={env['WEBHOOK_SECRET']}\n")
        print("Generated WEBHOOK_SECRET and saved it to local .env")
    required = ("PA_USERNAME", "PA_API_TOKEN", "TELEGRAM_BOT_TOKEN", "GROQ_API_KEY")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise DeployError(f"Missing in .env: {', '.join(missing)}")
    return env


def code_files() -> list[Path]:
    files = [ROOT / "wsgi.py"]
    files += [
        path for path in sorted((ROOT / "bot").glob("*.py")) if not path.name.startswith("test_")
    ]
    return files


def build_remote_env(env: dict[str, str], remote_dir: str, has_sa: bool) -> bytes:
    lines = [f"{key}={env[key]}" for key in RUNTIME_KEYS if env.get(key)]
    lines.append(f"DATA_DIR={remote_dir}/data")
    if has_sa:
        lines.append(f"GOOGLE_SERVICE_ACCOUNT_FILE={remote_dir}/{REMOTE_SA_NAME}")
    return ("\n".join(lines) + "\n").encode()


def build_wsgi(remote_dir: str) -> bytes:
    return (
        "import sys\n\n"
        f"PROJECT_DIR = {remote_dir!r}\n"
        "if PROJECT_DIR not in sys.path:\n"
        "    sys.path.insert(0, PROJECT_DIR)\n\n"
        "from wsgi import application  # noqa: E402,F401\n"
    ).encode()


def ensure_webapp(pa: PythonAnywhere, domain: str, remote_dir: str) -> None:
    webapps = pa.request("GET", "/webapps/", (200,)) or []
    existing = next((w for w in webapps if w.get("domain_name") == domain), None)
    major, minor = pa.best_python()
    if existing is None:
        print(f"Creating web app {domain} (Python {major}.{minor})")
        pa.request(
            "POST",
            "/webapps/",
            (200, 201),
            data={"domain_name": domain, "python_version": f"python{major}{minor}"},
        )
    pa.request(
        "PATCH",
        f"/webapps/{domain}/",
        (200,),
        data={
            "source_directory": remote_dir,
            "force_https": "true",
            "python_version": f"{major}.{minor}",
        },
    )
    for mapping in pa.request("GET", f"/webapps/{domain}/static_files/", (200,)) or []:
        if not str(mapping.get("path", "")).startswith(remote_dir):
            pa.request("DELETE", f"/webapps/{domain}/static_files/{mapping['id']}/", (204,))
            print(f"Removed stale static mapping {mapping.get('url')}")


def wait_healthy(url: str, attempts: int = 10) -> None:
    for _ in range(attempts):
        try:
            response = requests.get(url, timeout=TIMEOUT)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5)
    raise DeployError(f"Health check failed: {url}. See the error log on PythonAnywhere.")


def main() -> None:
    env = load_local_env()
    username = env["PA_USERNAME"]
    pa = PythonAnywhere(username, env["PA_API_TOKEN"], env.get("PA_HOST", "www.pythonanywhere.com"))
    domain = f"{username}.pythonanywhere.com"
    remote_dir = f"/home/{username}/telegram_ai_secretary"

    for path in code_files():
        relative = path.relative_to(ROOT).as_posix()
        pa.upload(f"{remote_dir}/{relative}", path.read_bytes())
        print(f"Uploaded {relative}")

    sa_local = env.get("GOOGLE_SERVICE_ACCOUNT_LOCAL_FILE", "")
    has_sa = bool(sa_local)
    if has_sa:
        pa.upload(f"{remote_dir}/{REMOTE_SA_NAME}", Path(sa_local).read_bytes())
        print("Uploaded Google service account key")
    pa.upload(f"{remote_dir}/.env", build_remote_env(env, remote_dir, has_sa))
    print("Uploaded .env")

    ensure_webapp(pa, domain, remote_dir)
    wsgi_path = f"/var/www/{domain.replace('.', '_')}_wsgi.py"
    pa.upload(wsgi_path, build_wsgi(remote_dir))
    print(f"Uploaded {wsgi_path}")

    pa.request("POST", f"/webapps/{domain}/reload/", (200,))
    print("Web app reloaded")
    wait_healthy(f"https://{domain}/healthz")
    print("Health check OK")

    telegram = TelegramClient(env["TELEGRAM_BOT_TOKEN"])
    telegram.set_webhook(
        f"https://{domain}/webhook/telegram", env["WEBHOOK_SECRET"], drop_pending=True
    )
    info = telegram.get_webhook_info()
    print(f"Webhook: {info.get('url')} | pending: {info.get('pending_update_count')}")


if __name__ == "__main__":
    try:
        main()
    except DeployError as exc:
        sys.exit(f"Deploy failed: {exc}")
