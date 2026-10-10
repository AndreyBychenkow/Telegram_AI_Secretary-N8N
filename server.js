'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// ============================================================================
// Конфигурация и переменные окружения
// ============================================================================
const PORT = Number(process.env.PORT || 5678);
const TELEGRAM_BOT_TOKEN = (process.env.TELEGRAM_BOT_TOKEN || '').trim();
const GROQ_API_KEY = (process.env.GROQ_API_KEY || '').trim();
const GOOGLE_SHEET_ID = (
  process.env.GOOGLE_SHEET_ID || '1wHrs5JeUsBoaCgu5ZvXOUh2ShHbaetiv22Tz12Hm7QA'
).trim();
const GOOGLE_CALENDAR_ID = (process.env.GOOGLE_CALENDAR_ID || 'primary').trim();

// Вариант 1 (самый простой): URL веб-приложения Google Apps Script
const GOOGLE_SCRIPT_URL = (process.env.GOOGLE_SCRIPT_URL || '').trim();

// Вариант 2 (опционально): JSON сервисного аккаунта Google
const GOOGLE_SERVICE_ACCOUNT_JSON = (process.env.GOOGLE_SERVICE_ACCOUNT_JSON || '').trim();

const GROQ_CHAT_MODEL = process.env.GROQ_CHAT_MODEL || 'llama-3.3-70b-versatile';
const GROQ_WHISPER_MODEL = process.env.GROQ_WHISPER_MODEL || 'whisper-large-v3-turbo';
const TIMEZONE = process.env.TZ || process.env.GENERIC_TIMEZONE || 'Europe/Moscow';

// Локальное хранилище (резерв + работа даже без настроенного Google API)
const DATA_DIR = path.join(__dirname, 'data');
const TASKS_FILE = path.join(DATA_DIR, 'tasks.json');
const LOGS_FILE = path.join(DATA_DIR, 'logs.json');

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function readJsonFile(filePath, fallback = []) {
  try {
    if (fs.existsSync(filePath)) {
      return JSON.parse(fs.readFileSync(filePath, 'utf8'));
    }
  } catch (err) {
    console.error(`[storage] Ошибка чтения ${filePath}:`, err.message);
  }
  return fallback;
}

function writeJsonFile(filePath, data) {
  try {
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf8');
  } catch (err) {
    console.error(`[storage] Ошибка записи ${filePath}:`, err.message);
  }
}

// Память диалога (Window Buffer Memory: последние 10 сообщений на каждый chat_id)
const chatMemory = new Map();
const MEMORY_WINDOW = 10;

function getChatHistory(chatId) {
  const key = String(chatId);
  if (!chatMemory.has(key)) {
    chatMemory.set(key, []);
  }
  return chatMemory.get(key);
}

function appendChatHistory(chatId, role, content) {
  const history = getChatHistory(chatId);
  history.push({ role, content });
  while (history.length > MEMORY_WINDOW) {
    history.shift();
  }
}

// ============================================================================
// Вспомогательные функции даты и времени (Europe/Moscow)
// ============================================================================
function getMoscowNowISO() {
  const now = new Date();
  const formatter = new Intl.DateTimeFormat('sv-SE', {
    timeZone: TIMEZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const parts = formatter.formatToParts(now).reduce((acc, p) => {
    acc[p.type] = p.value;
    return acc;
  }, {});
  const ms = String(now.getMilliseconds()).padStart(3, '0');
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}.${ms}+03:00`;
}

function getMoscowDateFormatted() {
  return new Intl.DateTimeFormat('ru-RU', {
    timeZone: TIMEZONE,
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date());
}

// ============================================================================
// Интеграция с Google (через Google Apps Script ИЛИ Service Account + локальный кэш)
// ============================================================================
let cachedGoogleToken = null;
let cachedGoogleTokenExp = 0;

async function getServiceAccountToken() {
  if (!GOOGLE_SERVICE_ACCOUNT_JSON) return null;
  const nowSec = Math.floor(Date.now() / 1000);
  if (cachedGoogleToken && cachedGoogleTokenExp > nowSec + 60) {
    return cachedGoogleToken;
  }
  try {
    const creds = JSON.parse(GOOGLE_SERVICE_ACCOUNT_JSON);
    const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
    const claim = Buffer.from(
      JSON.stringify({
        iss: creds.client_email,
        scope:
          'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/calendar',
        aud: 'https://oauth2.googleapis.com/token',
        exp: nowSec + 3600,
        iat: nowSec,
      })
    ).toString('base64url');
    const signInput = `${header}.${claim}`;
    const signer = crypto.createSign('RSA-SHA256');
    signer.update(signInput);
    const signature = signer.sign(creds.private_key, 'base64url');
    const jwt = `${signInput}.${signature}`;

    const res = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        assertion: jwt,
      }),
    });
    if (!res.ok) {
      throw new Error(`Google OAuth error ${res.status}: ${await res.text()}`);
    }
    const data = await res.json();
    cachedGoogleToken = data.access_token;
    cachedGoogleTokenExp = nowSec + (data.expires_in || 3600);
    return cachedGoogleToken;
  } catch (err) {
    console.error('[google] Ошибка авторизации сервисного аккаунта:', err.message);
    return null;
  }
}

async function addTaskToStorage({ created_at, chat_id, task, priority, deadline, status }) {
  const row = {
    created_at: created_at || getMoscowNowISO(),
    chat_id: String(chat_id),
    task: String(task || '').trim(),
    priority: String(priority || 'medium').toLowerCase(),
    deadline: String(deadline || '').trim(),
    status: String(status || 'new').toLowerCase(),
  };

  // 1. Всегда сохраняем локально
  const tasks = readJsonFile(TASKS_FILE, []);
  tasks.push(row);
  writeJsonFile(TASKS_FILE, tasks);

  // 2. Если настроен Google Apps Script URL
  if (GOOGLE_SCRIPT_URL) {
    try {
      await fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'addTask', data: row }),
      });
    } catch (err) {
      console.error('[google-script] Ошибка записи задачи:', err.message);
    }
  }

  // 3. Если настроен Google Service Account
  const token = await getServiceAccountToken();
  if (token && GOOGLE_SHEET_ID) {
    try {
      const url = `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Tasks!A:F:append?valueInputOption=USER_ENTERED`;
      await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          values: [
            [row.created_at, row.chat_id, row.task, row.priority, row.deadline, row.status],
          ],
        }),
      });
    } catch (err) {
      console.error('[google-sheets] Ошибка записи задачи:', err.message);
    }
  }

  return row;
}

async function readTasksFromStorage(chatId) {
  // 1. Пробуем прочитать из Google Apps Script
  if (GOOGLE_SCRIPT_URL) {
    try {
      const url = `${GOOGLE_SCRIPT_URL}?action=readTasks&chat_id=${encodeURIComponent(chatId || '')}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.tasks)) {
          return data.tasks;
        }
      }
    } catch (err) {
      console.error('[google-script] Ошибка чтения задач:', err.message);
    }
  }

  // 2. Пробуем прочитать через Google Service Account
  const token = await getServiceAccountToken();
  if (token && GOOGLE_SHEET_ID) {
    try {
      const url = `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Tasks!A1:F200`;
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        const rows = data.values || [];
        if (rows.length > 1) {
          const headers = rows[0];
          return rows.slice(1).map((r) => {
            const obj = {};
            headers.forEach((h, i) => {
              obj[h] = r[i] || '';
            });
            return obj;
          });
        }
      }
    } catch (err) {
      console.error('[google-sheets] Ошибка чтения задач:', err.message);
    }
  }

  // 3. Возвращаем из локального хранилища
  const allTasks = readJsonFile(TASKS_FILE, []);
  if (!chatId) return allTasks;
  const userTasks = allTasks.filter((t) => String(t.chat_id) === String(chatId));
  return userTasks.length > 0 ? userTasks : allTasks;
}

async function createCalendarEvent({ summary, start, end, description }) {
  const eventData = {
    summary: summary || 'Встреча',
    start,
    end,
    description: description || 'Создано Telegram AI-секретарём',
  };

  // 1. Через Google Apps Script
  if (GOOGLE_SCRIPT_URL) {
    try {
      await fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'createEvent', data: eventData }),
      });
    } catch (err) {
      console.error('[google-script] Ошибка создания встречи:', err.message);
    }
  }

  // 2. Через Google Service Account
  const token = await getServiceAccountToken();
  if (token) {
    try {
      const url = `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(
        GOOGLE_CALENDAR_ID
      )}/events`;
      await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          summary: eventData.summary,
          description: eventData.description,
          start: { dateTime: eventData.start, timeZone: TIMEZONE },
          end: { dateTime: eventData.end, timeZone: TIMEZONE },
        }),
      });
    } catch (err) {
      console.error('[google-calendar] Ошибка создания встречи:', err.message);
    }
  }

  return { status: 'created', ...eventData };
}

async function appendDialogueLog(logEntry) {
  const logs = readJsonFile(LOGS_FILE, []);
  logs.push(logEntry);
  if (logs.length > 500) logs.shift();
  writeJsonFile(LOGS_FILE, logs);

  if (GOOGLE_SCRIPT_URL) {
    try {
      await fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'addLog', data: logEntry }),
      });
    } catch (err) {
      console.error('[google-script] Ошибка записи лога:', err.message);
    }
  }

  const token = await getServiceAccountToken();
  if (token && GOOGLE_SHEET_ID) {
    try {
      const url = `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Logs!A:H:append?valueInputOption=USER_ENTERED`;
      await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          values: [
            [
              logEntry.timestamp,
              logEntry.chat_id,
              logEntry.user_id,
              logEntry.username,
              logEntry.input_type,
              logEntry.user_message,
              logEntry.assistant_reply,
              logEntry.calendar_command,
            ],
          ],
        }),
      });
    } catch (err) {
      console.error('[google-sheets] Ошибка записи лога:', err.message);
    }
  }
}

// ============================================================================
// Telegram Bot API + Groq Whisper (транскрибация голоса)
// ============================================================================
async function sendTelegramMessage(chatId, text) {
  if (!TELEGRAM_BOT_TOKEN) {
    console.warn('[telegram] Не задан TELEGRAM_BOT_TOKEN');
    return;
  }
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chat_id: chatId,
      text,
    }),
  });
  if (!res.ok) {
    console.error('[telegram] Ошибка отправки сообщения:', await res.text());
  }
}

async function transcribeTelegramVoice(fileId) {
  // 1. Получаем путь к файлу в Telegram
  const fileInfoRes = await fetch(
    `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getFile?file_id=${encodeURIComponent(fileId)}`
  );
  const fileInfo = await fileInfoRes.json();
  if (!fileInfo.ok || !fileInfo.result?.file_path) {
    throw new Error('Не удалось получить голосовой файл из Telegram');
  }

  // 2. Скачиваем .oga/.ogg аудиофайл
  const fileUrl = `https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/${fileInfo.result.file_path}`;
  const audioRes = await fetch(fileUrl);
  const arrayBuffer = await audioRes.arrayBuffer();

  // 3. Подготавливаем audio metadata (voice.ogg, audio/ogg) и отправляем в Groq Whisper
  const formData = new FormData();
  const audioBlob = new Blob([arrayBuffer], { type: 'audio/ogg' });
  formData.append('file', audioBlob, 'voice.ogg');
  formData.append('model', GROQ_WHISPER_MODEL);
  formData.append('language', 'ru');

  const whisperRes = await fetch('https://api.groq.com/openai/v1/audio/transcriptions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${GROQ_API_KEY}`,
    },
    body: formData,
  });

  if (!whisperRes.ok) {
    const errText = await whisperRes.text();
    throw new Error(`Groq Whisper error ${whisperRes.status}: ${errText}`);
  }

  const whisperData = await whisperRes.json();
  return (whisperData.text || '').trim();
}

// ============================================================================
// AI-агент (Groq Chat Model + Tools + Память диалога)
// ============================================================================
const AGENT_TOOLS = [
  {
    type: 'function',
    function: {
      name: 'GoogleSheets_Add_Task',
      description: 'Добавить новую задачу пользователя в таблицу (лист Tasks).',
      parameters: {
        type: 'object',
        properties: {
          task: {
            type: 'string',
            description: 'Описание задачи (например: подготовить презентацию)',
          },
          priority: {
            type: 'string',
            enum: ['high', 'medium', 'low'],
            description: 'Приоритет задачи на английском: high, medium или low',
          },
          deadline: {
            type: 'string',
            description: 'Срок выполнения в формате YYYY-MM-DD (например: 2026-09-01)',
          },
          status: {
            type: 'string',
            description: 'Статус задачи (по умолчанию new)',
          },
        },
        required: ['task', 'priority', 'deadline'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'GoogleSheets_Read_Tasks',
      description: 'Прочитать список сохранённых задач пользователя из листа Tasks.',
      parameters: {
        type: 'object',
        properties: {},
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'GoogleCalendar_Create_Event',
      description: 'Запланировать встречу или событие в Google Calendar.',
      parameters: {
        type: 'object',
        properties: {
          summary: {
            type: 'string',
            description: 'Название встречи (например: обсуждение проекта)',
          },
          start: {
            type: 'string',
            description:
              'Дата и время начала в формате ISO 8601 с часовым поясом +03:00 (например: 2026-09-02T15:00:00+03:00)',
          },
          end: {
            type: 'string',
            description:
              'Дата и время окончания в формате ISO 8601 с часовым поясом +03:00 (например: 2026-09-02T16:00:00+03:00)',
          },
        },
        required: ['summary', 'start', 'end'],
      },
    },
  },
];

async function runAiAgent(chatId, userText) {
  const systemPrompt = `Ты — дружелюбный и лаконичный AI-помощник и личный секретарь в Telegram.
Текущая дата и время (Москва, UTC+3): ${getMoscowNowISO()} (${getMoscowDateFormatted()}).
ID текущего чата (chat_id): ${chatId}.

Твои правила работы:
1. ДОБАВЛЕНИЕ ЗАДАЧИ:
   Когда пользователь просит добавить задачу, обязательно вызови инструмент GoogleSheets_Add_Task.
   После вызова инструмента ответь строго в таком стиле:
   ✅ Задача «<название>» добавлена в список.
   📅 Срок: <дата по-русски>
   ⚡ Приоритет: <высокий/средний/низкий>

   Если понадобится уточнить детали или добавить другие задачи — дайте знать!

2. СПИСОК ЗАДАЧ:
   Когда пользователь спрашивает, какие задачи у него сохранены, обязательно вызови инструмент GoogleSheets_Read_Tasks.
   Никогда не используй Markdown-таблицы! Форматируй ответ списком:
   📋 Список задач

   1. 📝 Задача: <название>
      ⚡ Приоритет: <высокий/средний/низкий>
      📅 Срок: <ДД.ММ.ГГГГ>
      📌 Статус: <новый>

3. СОЗДАНИЕ ВСТРЕЧИ В КАЛЕНДАРЕ:
   Когда пользователь просит запланировать встречу или событие, вызови инструмент GoogleCalendar_Create_Event (если длительность не указана, ставь 1 час).
   После вызова ответь строго в таком стиле:
   ✅ Встреча «<название>» успешно создана в календаре.

   📅 Дата: <дата по-русски>
   🕒 Время: <ЧЧ:ММ – ЧЧ:ММ>
   ⏱️ Продолжительность: <длительность>

   Если понадобится изменить детали или добавить участников — дайте знать!

4. ОБЫЧНЫЙ ДИАЛОГ:
   Отвечай дружелюбно, коротко и естественно по-русски.`;

  const history = getChatHistory(chatId);
  const messages = [{ role: 'system', content: systemPrompt }, ...history, { role: 'user', content: userText }];

  const callGroq = async (msgs, useTools = true) => {
    const body = {
      model: GROQ_CHAT_MODEL,
      messages: msgs,
      temperature: 0.3,
    };
    if (useTools) {
      body.tools = AGENT_TOOLS;
      body.tool_choice = 'auto';
    }
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${GROQ_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`Groq Chat error ${res.status}: ${errText}`);
    }
    return res.json();
  };

  let completion = await callGroq(messages, true);
  let choice = completion.choices?.[0]?.message;

  if (choice?.tool_calls && choice.tool_calls.length > 0) {
    messages.push(choice);

    for (const toolCall of choice.tool_calls) {
      const fnName = toolCall.function?.name;
      let args = {};
      try {
        args = JSON.parse(toolCall.function?.arguments || '{}');
      } catch (_) {
        args = {};
      }

      let toolResult = {};
      if (fnName === 'GoogleSheets_Add_Task') {
        toolResult = await addTaskToStorage({
          created_at: getMoscowNowISO(),
          chat_id: chatId,
          task: args.task,
          priority: args.priority || 'medium',
          deadline: args.deadline || '',
          status: args.status || 'new',
        });
      } else if (fnName === 'GoogleSheets_Read_Tasks') {
        const tasks = await readTasksFromStorage(chatId);
        toolResult = { tasks };
      } else if (fnName === 'GoogleCalendar_Create_Event') {
        toolResult = await createCalendarEvent({
          summary: args.summary,
          start: args.start,
          end: args.end,
        });
      }

      messages.push({
        role: 'tool',
        tool_call_id: toolCall.id,
        name: fnName,
        content: JSON.stringify(toolResult),
      });
    }

    completion = await callGroq(messages, false);
    choice = completion.choices?.[0]?.message;
  }

  const replyText = (choice?.content || 'Готово! Чем ещё могу помочь?').trim();
  appendChatHistory(chatId, 'user', userText);
  appendChatHistory(chatId, 'assistant', replyText);
  return replyText;
}

// ============================================================================
// Полный пайплайн обработки сообщения из Telegram (по ARCHITECTURE.md)
// ============================================================================
async function processTelegramUpdate(update) {
  const msg = update.message || update.edited_message;
  if (!msg || !msg.chat) return;

  // 1. Нормализация входа
  const chatId = msg.chat.id;
  const userId = msg.from?.id || chatId;
  const username = msg.from?.username || msg.from?.first_name || 'user';
  const isVoice = Boolean(msg.voice);
  const isText = Boolean(msg.text);
  const inputType = isVoice ? 'voice' : isText ? 'text' : 'other';

  // 2. Отсечение неподдерживаемых типов
  if (inputType === 'other') {
    await sendTelegramMessage(
      chatId,
      'Извините, я поддерживаю только текстовые и голосовые сообщения 🙂 Напишите текстом или отправьте голосовое сообщение.'
    );
    return;
  }

  if (!GROQ_API_KEY) {
    await sendTelegramMessage(
      chatId,
      '⚠️ На сервере не задан GROQ_API_KEY. Добавьте его в раздел Environment на Render.com.'
    );
    return;
  }

  try {
    // 3. Подготовка userText (текст напрямую или голос через Groq Whisper)
    let userText = '';
    if (inputType === 'voice') {
      userText = await transcribeTelegramVoice(msg.voice.file_id);
    } else {
      userText = (msg.text || '').trim();
    }

    if (!userText) {
      await sendTelegramMessage(chatId, 'Не удалось распознать сообщение. Попробуйте ещё раз!');
      return;
    }

    // 4. Определение команды (calendar_command = yes/no)
    const isCalendar = /(запланир|встреч|календар|созвон|событи|переговор)/i.test(userText);
    const calendarCommand = isCalendar ? 'yes' : 'no';

    // 5. Вызов AI-агента
    const assistantReply = await runAiAgent(chatId, userText);

    // 6. Отправка ответа в Telegram
    await sendTelegramMessage(chatId, assistantReply);

    // 7. Запись в Logs
    await appendDialogueLog({
      timestamp: new Date().toISOString(),
      chat_id: chatId,
      user_id: userId,
      username,
      input_type: inputType,
      user_message: userText,
      assistant_reply: assistantReply,
      calendar_command: calendarCommand,
    });
  } catch (err) {
    console.error('[pipeline] Ошибка обработки сообщения:', err);
    await sendTelegramMessage(
      chatId,
      `Произошла временная ошибка при обработке запроса: ${err.message}`
    );
  }
}

// ============================================================================
// Автоматическое подключение Telegram Webhook или Long Polling
// ============================================================================
let pollingActive = false;
let lastUpdateId = 0;

async function startPollingFallback() {
  if (pollingActive || !TELEGRAM_BOT_TOKEN) return;
  pollingActive = true;
  console.log('[telegram] Запуск режима получения сообщений...');

  // Сначала удаляем старый вебхук (например, от старого n8n.cloud), если нет внешнего URL
  const externalUrl = (process.env.RENDER_EXTERNAL_URL || process.env.WEBHOOK_URL || '').replace(
    /\/+$/,
    ''
  );

  if (externalUrl) {
    try {
      const webhookUrl = `${externalUrl}/webhook/telegram`;
      const res = await fetch(
        `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook?url=${encodeURIComponent(
          webhookUrl
        )}`
      );
      const data = await res.json();
      if (data.ok) {
        console.log(`[telegram] Webhook успешно установлен на: ${webhookUrl}`);
        return;
      }
    } catch (err) {
      console.error('[telegram] Ошибка установки webhook, переключаюсь на polling:', err.message);
    }
  }

  // Если внешний URL не задан, используем Long Polling
  try {
    await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook`);
  } catch (_) {}

  const poll = async () => {
    while (pollingActive) {
      try {
        const res = await fetch(
          `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates?offset=${
            lastUpdateId + 1
          }&timeout=25`
        );
        const data = await res.json();
        if (data.ok && Array.isArray(data.result)) {
          for (const upd of data.result) {
            lastUpdateId = Math.max(lastUpdateId, upd.update_id);
            await processTelegramUpdate(upd);
          }
        }
      } catch (err) {
        console.error('[polling] Ошибка:', err.message);
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
  };
  poll();
}

// ============================================================================
// Веб-сервер (Статус-панель + Webhook + /healthz для Render)
// ============================================================================
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', memoryMB: Math.round(process.memoryUsage().rss / 1024 / 1024) }));
    return;
  }

  if (req.method === 'POST' && url.pathname.startsWith('/webhook')) {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
    });
    req.on('end', () => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      try {
        const update = JSON.parse(body);
        processTelegramUpdate(update);
      } catch (err) {
        console.error('[webhook] Ошибка парсинга JSON:', err.message);
      }
    });
    return;
  }

  // Главная страница — понятная панель управления на русском языке
  const memMB = Math.round(process.memoryUsage().rss / 1024 / 1024);
  const tasks = readJsonFile(TASKS_FILE, []);
  const logs = readJsonFile(LOGS_FILE, []).slice(-10).reverse();

  const tgOk = Boolean(TELEGRAM_BOT_TOKEN);
  const groqOk = Boolean(GROQ_API_KEY);
  const googleOk = Boolean(GOOGLE_SCRIPT_URL || GOOGLE_SERVICE_ACCOUNT_JSON);

  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(`<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Telegram AI Secretary — Панель управления</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }
    .container { max-width: 900px; margin: 0 auto; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
    h1 { margin-top: 0; font-size: 24px; }
    h2 { font-size: 18px; margin-top: 0; color: #94a3b8; }
    .badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 13px; font-weight: 600; }
    .ok { background: #065f46; color: #6ee7b7; }
    .warn { background: #78350f; color: #fcd34d; }
    .err { background: #7f1d1d; color: #fca5a5; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #334155; }
    th { color: #94a3b8; }
    a { color: #38bdf8; }
    code { background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #e2e8f0; }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>🤖 Telegram AI Secretary (@my_032_secretary_bot)</h1>
      <p>Сервер работает в облегчённом режиме (без падений 502). Потребление памяти: <b>${memMB} МБ из 512 МБ</b>.</p>
      <p>
        Telegram Bot Token: <span class="badge ${tgOk ? 'ok' : 'err'}">${tgOk ? '✅ Подключён' : '❌ Не задан TELEGRAM_BOT_TOKEN'}</span>
        &nbsp; Groq API: <span class="badge ${groqOk ? 'ok' : 'err'}">${groqOk ? '✅ Подключён' : '❌ Не задан GROQ_API_KEY'}</span>
        &nbsp; Хранилище: <span class="badge ${googleOk ? 'ok' : 'warn'}">${googleOk ? '✅ Google Sheets + Calendar' : '⚡ Встроенная память + Google ID ' + GOOGLE_SHEET_ID.slice(0, 8) + '...'}</span>
      </p>
    </div>

    ${
      !tgOk || !groqOk
        ? `<div class="card" style="border-color:#ef4444">
      <h2>⚠️ Что осталось добавить в Render → Environment:</h2>
      <ol>
        ${!tgOk ? '<li>Добавьте переменную <code>TELEGRAM_BOT_TOKEN</code> = токен вашего бота <code>@my_032_secretary_bot</code></li>' : ''}
        ${!groqOk ? '<li>Добавьте переменную <code>GROQ_API_KEY</code> = ваш ключ <code>gsk_...</code></li>' : ''}
      </ol>
    </div>`
        : ''
    }

    <div class="card">
      <h2>📋 Сохранённые задачи (${tasks.length})</h2>
      ${
        tasks.length === 0
          ? '<p style="color:#94a3b8">Задач пока нет. Напишите боту: «Добавь задачу: подготовить презентацию к пятнице, приоритет высокий».</p>'
          : `<table>
        <thead><tr><th>Дата</th><th>Задача</th><th>Приоритет</th><th>Срок</th><th>Статус</th></tr></thead>
        <tbody>
          ${tasks
            .map(
              (t) =>
                `<tr><td>${t.created_at}</td><td>${t.task}</td><td>${t.priority}</td><td>${t.deadline}</td><td>${t.status}</td></tr>`
            )
            .join('')}
        </tbody>
      </table>`
      }
    </div>

    <div class="card">
      <h2>🧾 Последние диалоги (${logs.length})</h2>
      ${
        logs.length === 0
          ? '<p style="color:#94a3b8">Диалогов пока нет. Напишите боту «Привет» в Telegram!</p>'
          : `<table>
        <thead><tr><th>Время</th><th>Пользователь</th><th>Тип</th><th>Сообщение</th><th>Ответ бота</th></tr></thead>
        <tbody>
          ${logs
            .map(
              (l) =>
                `<tr><td>${l.timestamp}</td><td>@${l.username}</td><td>${l.input_type}</td><td>${l.user_message}</td><td>${l.assistant_reply}</td></tr>`
            )
            .join('')}
        </tbody>
      </table>`
      }
    </div>
  </div>
</body>
</html>`);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[server] Telegram AI Secretary запущен на порту ${PORT}`);
  startPollingFallback();
});
