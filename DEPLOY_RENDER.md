# 🚀 Пошаговый деплой Telegram AI Secretary на Render.com

В репозиторий уже добавлены все необходимые файлы для деплоя бесплатной версии **n8n Community Edition** на Render.com с автоматическим импортом вашего workflow:

- **`workflow.json`** — полный workflow `Telegram AI секретарь: текст/голос + Calendar + Sheets`, где уже прописан ID вашей Google-таблицы (`1wHrs5JeUsBoaCgu5ZvXOUh2ShHbaetiv22Tz12Hm7QA`) и листы `Tasks` / `Logs`.
- **`Dockerfile`** и **`docker-entrypoint.sh`** — собирают образ n8n, автоматически настраивают `WEBHOOK_URL` и `N8N_HOST` под домен Render и при старте автоматически импортируют `workflow.json`.
- **`render.yaml`** — Blueprint-конфигурация для деплоя на Render в 1 клик.
- **`.env.example`** — шаблон переменных окружения.

---

## Шаг 1. Подготовьте бесплатную базу данных PostgreSQL (рекомендуется для Free-тарифа Render)

На бесплатном тарифе Render контейнеры не имеют постоянного диска: при перезапуске контейнера локальная база SQLite очищается. Чтобы **не вводить заново ключи (Credentials) и аккаунт n8n** после каждого перезапуска:

1. Зарегистрируйтесь на бесплатном облачном PostgreSQL-хостинге **[Neon.tech](https://neon.tech)** (бесплатный тариф **не удаляется** через 30 дней, в отличие от бесплатной БД на самом Render).
2. Создайте проект и скопируйте параметры подключения (`Host`, `Database`, `User`, `Password`).

*(Если вы используете платный тариф Render Starter с подключённым диском `/home/node/.n8n`, этот шаг можно пропустить).*

---

## Шаг 2. Задеплойте репозиторий на Render.com

### Способ А — Автоматически через Blueprint (`render.yaml`)
1. Откройте [dashboard.render.com](https://dashboard.render.com).
2. Нажмите **New +** → **Blueprint**.
3. Выберите ваш репозиторий **`AndreyBychenkow/Telegram_AI_Secretary-N8N`**.
4. Render автоматически прочитает файл `render.yaml` и попросит ввести значение для переменной:
   - `GROQ_API_KEY` — вставьте ваш ключ Groq (`gsk_...`).
5. Нажмите **Apply** / **Create**.

### Способ Б — Через обычный Web Service
1. В [dashboard.render.com](https://dashboard.render.com) нажмите **New +** → **Web Service**.
2. Подключите репозиторий **`AndreyBychenkow/Telegram_AI_Secretary-N8N`**.
3. Убедитесь, что **Runtime** выбран **Docker**, а **Instance Type** — **Free**.
4. В разделе **Environment Variables** добавьте:
   - `GROQ_API_KEY` = `ваш_ключ_gsk_...`
   - `N8N_ENCRYPTION_KEY` = `любая_случайная_строка_из_32_символов`
   *(Переменные `WEBHOOK_URL`, `N8N_HOST` и `N8N_PORT` скрипт `docker-entrypoint.sh` подставит автоматически из системных переменных Render!)*
5. Если вы создали базу PostgreSQL на Шаге 1, добавьте также переменные БД:
   - `DB_TYPE` = `postgresdb`
   - `DB_POSTGRESDB_HOST` = `<ваш хост Neon/Postgres>`
   - `DB_POSTGRESDB_PORT` = `5432`
   - `DB_POSTGRESDB_DATABASE` = `<имя базы>`
   - `DB_POSTGRESDB_USER` = `<пользователь>`
   - `DB_POSTGRESDB_PASSWORD` = `<пароль>`
   - `DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED` = `false`
6. Нажмите **Deploy Web Service**.

Подождите 2–4 минуты, пока соберётся Docker-контейнер и в логах появится строка:
`Editor is now accessible via: https://...onrender.com`

---

## Шаг 3. Первичный вход в ваш n8n и привязка ключей

1. Перейдите по вашей ссылке `https://<имя-сервиса>.onrender.com`.
2. Создайте локальную учётную запись администратора (Owner).
3. В разделе **Workflows** вы сразу увидите уже импортированный workflow:
   **«Telegram AI секретарь: текст/голос + Calendar + Sheets»**!
   *(Если по какой-то причине его нет — нажмите **Add Workflow → Import from File...** и выберите файл `workflow.json` из этого репозитория).*

4. Создайте 4 учётные записи в разделе **Credentials → Add Credential**:
   - **Telegram API**:
     - Вставьте Bot Token вашего бота `@my_032_secretary_bot` (от `@BotFather`).
   - **Groq API**:
     - Вставьте ваш API-ключ `gsk_...`.
   - **Google Sheets OAuth2 API** и **Google Calendar OAuth2 API**:
     - В [Google Cloud Console](https://console.cloud.google.com/apis/credentials) создайте **OAuth client ID** (тип *Web application*).
     - В поле **Authorized redirect URIs** добавьте адрес из окна credential в n8n:
       `https://<имя-сервиса>.onrender.com/rest/oauth2-credential/callback`
     - Включите **Google Sheets API** и **Google Calendar API** в разделе *APIs & Services → Library*.
     - Скопируйте `Client ID` и `Client Secret` в n8n и нажмите **Sign in with Google**.

5. Откройте workflow **«Telegram AI секретарь: текст/голос + Calendar + Sheets»**:
   - Выберите созданные Credentials в нодах Telegram, Groq Chat Model, Google Calendar и Google Sheets (ID вашей таблицы `1wHrs5JeUsBoaCgu5ZvXOUh2ShHbaetiv22Tz12Hm7QA` там уже прописан!).
   - В правом верхнем углу переключите тумблер в положение **Active** (Published).

Готово! Начиная с этого момента n8n сам перерегистрирует webhook вашего бота `@my_032_secretary_bot` со старого `decebell032.app.n8n.cloud` на ваш новый адрес `https://<имя-сервиса>.onrender.com`.

---

## 💡 Как сделать так, чтобы бесплатный Render не «засыпал»

На бесплатном тарифе Render усыпляет сервис, если к нему не было запросов 15 минут. Чтобы бот отвечал мгновенно 24/7:
1. Зарегистрируйтесь на бесплатном сервисе [cron-job.org](https://cron-job.org) или [UptimeRobot](https://uptimerobot.com).
2. Создайте HTTP-монитор на адрес:
   `https://<имя-сервиса>.onrender.com/healthz`
   с интервалом **каждые 10 минут**.
