import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        self.max_bot_token = os.getenv("MAX_BOT_TOKEN", "").strip()
        self.max_api_base_url = os.getenv("MAX_API_BASE_URL", "https://platform-api.max.ru").rstrip("/")
        self.backend_login_url = os.getenv("BACKEND_LOGIN_URL", "http://127.0.0.1:8080/api/v1/auth/max/login").strip()
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8090"))
        self.poll_timeout_seconds = int(os.getenv("POLL_TIMEOUT_SECONDS", "25"))
        self.bot_start_text = os.getenv(
            "BOT_START_TEXT",
            "Бот запущен. Если мини-приложение уже привязано к боту в кабинете MAX, откройте его кнопкой на карточке бота.",
        ).strip()


settings = Settings()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="MAX Test Bot")


class LoginPayload(BaseModel):
    init_data: str


class MaxBotClient:
    def __init__(self, token: str, api_base_url: str, poll_timeout_seconds: int) -> None:
        self.token = token
        self.api_base_url = api_base_url
        self.poll_timeout_seconds = poll_timeout_seconds
        self.marker: int | None = None
        self.client = httpx.AsyncClient(
            base_url=api_base_url,
            timeout=httpx.Timeout(timeout=poll_timeout_seconds + 10),
            headers={"Authorization": token},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def get_me(self) -> dict[str, Any]:
        response = await self.client.get("/me")
        response.raise_for_status()
        return response.json()

    async def get_updates(self) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.poll_timeout_seconds,
            "limit": 100,
            "types": "message_created",
        }
        if self.marker is not None:
            params["marker"] = self.marker

        response = await self.client.get("/updates", params=params)
        response.raise_for_status()
        payload = response.json()

        updates = payload.get("updates") or []
        marker = payload.get("marker")
        if marker is not None:
            try:
                self.marker = int(marker)
            except (TypeError, ValueError):
                pass

        return updates

    async def send_message_to_user(self, user_id: int, text: str) -> None:
        response = await self.client.post(
            "/messages",
            params={"user_id": user_id},
            json={"text": text},
        )
        response.raise_for_status()


bot_client: MaxBotClient | None = None
polling_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup() -> None:
    global bot_client, polling_task

    if not settings.max_bot_token:
        raise RuntimeError("MAX_BOT_TOKEN is required")

    bot_client = MaxBotClient(
        token=settings.max_bot_token,
        api_base_url=settings.max_api_base_url,
        poll_timeout_seconds=settings.poll_timeout_seconds,
    )

    me = await bot_client.get_me()
    print(f"[max-bot] connected as: {me}")
    polling_task = asyncio.create_task(poll_updates_loop(bot_client))


@app.on_event("shutdown")
async def shutdown() -> None:
    global bot_client, polling_task

    if polling_task is not None:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

    if bot_client is not None:
        await bot_client.close()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def render_miniapp(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "miniapp.html",
        {
            "request": request,
            "backend_login_url": settings.backend_login_url,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def miniapp_root(request: Request) -> HTMLResponse:
    return render_miniapp(request)


@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp(request: Request) -> HTMLResponse:
    return render_miniapp(request)


@app.get("/miniapp/", response_class=HTMLResponse)
async def miniapp_slash(request: Request) -> HTMLResponse:
    return render_miniapp(request)


@app.post("/api/login")
async def login(payload: LoginPayload) -> JSONResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=20)) as client:
        try:
            response = await client.post(
                settings.backend_login_url,
                json={"init_data": payload.init_data},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"backend request failed: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return JSONResponse(status_code=response.status_code, content=response.json())

    return JSONResponse(status_code=response.status_code, content={"raw": response.text})


async def poll_updates_loop(client: MaxBotClient) -> None:
    while True:
        try:
            updates = await client.get_updates()
            for update in updates:
                await handle_update(client, update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            print(f"[max-bot] polling error: {exc}")
            await asyncio.sleep(2)


async def handle_update(client: MaxBotClient, update: dict[str, Any]) -> None:
    update_type = update.get("update_type") or update.get("type")
    if update_type != "message_created":
        return

    message = update.get("message") or update.get("body") or {}
    if not isinstance(message, dict):
        return

    sender = message.get("sender") or {}
    if not isinstance(sender, dict):
        return

    user_id = sender.get("user_id")
    if not isinstance(user_id, int):
        return

    text = extract_message_text(message)
    if text.startswith("/start"):
        reply = build_start_text()
    else:
        reply = build_default_text(text)

    await client.send_message_to_user(user_id, reply)


def extract_message_text(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    if isinstance(body, dict):
        text = body.get("text")
        if isinstance(text, str):
            return text.strip()

    text = message.get("text")
    if isinstance(text, str):
        return text.strip()

    return ""


def build_start_text() -> str:
    lines = [
        settings.bot_start_text,
        "",
        "Что дальше:",
        "1. В кабинете MAX привяжите mini app URL к этому боту.",
        f"2. Укажите URL: {settings.public_base_url + '/miniapp' if settings.public_base_url else '<PUBLIC_BASE_URL>/miniapp'}",
        "3. Откройте mini app из MAX.",
        "4. На странице mini app нажмите Login in backend.",
    ]
    return "\n".join(lines)


def build_default_text(text: str) -> str:
    if text:
        return (
            "Бот работает.\n\n"
            "Чтобы проверить auth, открой mini app, привязанный к боту в кабинете MAX.\n"
            "Внутри mini app страница покажет initData и сможет отправить его в backend.\n\n"
            f"Последнее сообщение: {text}"
        )

    return "Бот работает. Для проверки auth нужен mini app, привязанный к этому боту."


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.host, port=settings.port, reload=False)
