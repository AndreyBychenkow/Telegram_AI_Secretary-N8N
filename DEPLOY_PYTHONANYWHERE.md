# Деплой на PythonAnywhere (бесплатный аккаунт)

Бот работает как Flask-приложение (WSGI) и получает сообщения через Telegram webhook:
`https://<username>.pythonanywhere.com/webhook/telegram`. Запросы без заголовка
`X-Telegram-Bot-Api-Secret-Token` отклоняются с кодом 403.

## Структура

```text
bot/
├── app.py           # Flask: /healthz, /webhook/telegram
├── pipeline.py      # обработка update: текст / голос → агент → ответ → лог
├── agent.py         # Groq chat + tools (задачи, список задач, встречи) + память
├── groq_api.py      # Groq Chat / Whisper, retry на 429
├── telegram_api.py  # Telegram Bot API
├── storage.py       # SQLite: tasks, logs, events, memory, dedupe update_id
├── repository.py    # SQLite + зеркалирование в Google (если подключён)
├── google_api.py    # Google Sheets / Calendar через service account
├── config.py        # настройки из окружения / .env
└── tests/
deploy/deploy_pythonanywhere.py  # деплой через PythonAnywhere API
wsgi.py                          # WSGI entrypoint
```

На PythonAnywhere нужные пакеты (Flask, requests, google-auth) уже предустановлены, `pip install` не нужен.

## Деплой

1. Получить API-токен PythonAnywhere: Account → API Token → Create.
2. Заполнить локальный `.env` (он в `.gitignore`) по образцу `.env.example`:
   `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `PA_USERNAME`, `PA_API_TOKEN`.
   `WEBHOOK_SECRET` сгенерируется сам при первом деплое.
3. Запустить:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe deploy\deploy_pythonanywhere.py
```

Скрипт загружает код и `.env` в `/home/<username>/telegram_ai_secretary`, создаёт/настраивает
web app, перезагружает его, проверяет `/healthz` и ставит Telegram webhook.
Повторный запуск = обновление кода.

## Ограничения бесплатного тарифа

- Web app нужно продлевать раз в месяц (кнопка **Run until 1 month from today** на вкладке **Web**),
  иначе сайт отключится и бот перестанет отвечать. Это автоматизировано, см. ниже.
- 1 web worker: сообщения обрабатываются по очереди.
- Исходящие запросы только к allowlist (Telegram, Groq, `*.googleapis.com` в нём есть).

## Автопродление web app

GitHub Actions `.github/workflows/renew-pythonanywhere.yml` раз в 5 дней запускает
`deploy/renew_pythonanywhere.py`: логин на PythonAnywhere → кнопка продления → вывод нового срока.

Настройка: GitHub → репозиторий → Settings → Secrets and variables → Actions → New repository secret:

- `PA_USERNAME` — логин PythonAnywhere;
- `PA_PASSWORD` — пароль PythonAnywhere.

Проверка: вкладка **Actions** → *Renew PythonAnywhere web app* → **Run workflow**.

Ограничения:

- Не работает, если на PythonAnywhere включена двухфакторная авторизация.
- GitHub отключает расписание после 60 дней без активности в репо; workflow сам делает
  служебный коммит `.github/renew-heartbeat`, если коммитов не было 45 дней.
- Локальный запуск: `PA_PASSWORD` в `.env`, затем
  `.\.venv\Scripts\python.exe deploy\renew_pythonanywhere.py`.

## Google Sheets / Calendar (опционально)

Без Google бот хранит задачи, логи и встречи в SQLite (`data/bot.sqlite3` на сервере).

Подключение:

1. Google Cloud Console → создать проект → включить **Google Sheets API** и **Google Calendar API**.
2. IAM → Service Accounts → создать аккаунт → Keys → Add key → JSON. Сохранить файл вне репозитория.
3. Открыть таблицу → «Поделиться» → email сервисного аккаунта → **Редактор**.
   Листы `Tasks` (A:F) и `Logs` (A:H) с заголовками, как в `ARCHITECTURE.md`.
4. Google Calendar → настройки календаря → «Открыть доступ» → email сервисного аккаунта →
   **Внесение изменений в мероприятия**. ID календаря — в разделе «Интеграция календаря».
5. В `.env`: `GOOGLE_SERVICE_ACCOUNT_LOCAL_FILE=<путь к JSON>`, `GOOGLE_SHEET_ID`, `GOOGLE_CALENDAR_ID`.
6. Повторить деплой.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m ruff format --check bot deploy wsgi.py
.\.venv\Scripts\python.exe -m ruff check bot deploy wsgi.py
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest
```

Логи сервера: вкладка **Web** → Error log.
