# MAX test bot

Локальный Python-сервис для проверки MAX mini app flow.

Что делает:

- поднимает long polling бота через MAX Bot API
- отвечает на `/start` и любые текстовые сообщения
- отдаёт тестовую mini app страницу на `/miniapp`
- показывает `window.WebApp.initData`
- умеет проксировать `init_data` в backend `POST /api/v1/auth/max/login`

## Что всё равно нужно сделать руками в MAX

Это важно: код не может сам создать бота и привязать mini app в кабинете MAX.

Нужно руками:

1. Создать бота в кабинете MAX для партнёров.
2. Получить bot token.
3. Указать публичный URL mini app:
   `https://<твой-public-host>/miniapp`
4. Сохранить настройки mini app у бота.

Официальная документация MAX:

- mini apps: https://dev.max.ru/help/miniapps
- валидация `WebAppData / initData`: https://dev.max.ru/docs/webapps/validation
- bot API overview: https://dev.max.ru/docs-api

## Локальный запуск

```bash
cd internal/infrastructure/max/bot
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Если у бота уже настроен webhook, для long polling его лучше отключить. По документации MAX long polling подходит для dev/test, webhook — для production.

## Запуск через Docker

```bash
cd internal/infrastructure/max/bot
cp .env.example .env
docker compose up -d --build
```

Проверка:

```bash
curl http://127.0.0.1:8090/healthz
```

## Как проверять auth flow

1. Подними backend на `http://127.0.0.1:8080`
2. Подними этот бот-сервис
3. Пробрось публичный HTTPS URL на бот-сервис, например через `ngrok` или `cloudflared`
4. Вставь публичный URL в настройках mini app у бота:
   `https://<public-host>/miniapp`
5. Открой mini app из MAX
6. На странице `/miniapp` нажми `Login in backend`

Для production лучше отдавать bot/mini app через обычный `https://домен/...` на `443` за `nginx`/`caddy`, а не через нестандартный внешний порт. Так меньше шансов уткнуться в ограничения webview, firewall и предупреждения браузера.
7. Страница покажет backend response с твоим JWT

## Переменные окружения

- `MAX_BOT_TOKEN` — токен бота MAX
- `MAX_API_BASE_URL` — базовый URL Bot API
- `BACKEND_LOGIN_URL` — backend endpoint для login
- `PUBLIC_BASE_URL` — публичный URL этого сервиса
- `HOST`, `PORT` — где поднимать FastAPI
- `POLL_TIMEOUT_SECONDS` — timeout long polling
- `BOT_START_TEXT` — текст ответа бота
