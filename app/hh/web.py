import asyncio
import hmac
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.hh.client import NEW_NEGOTIATION_ACTION, WEBHOOK_PATH, HHClient
from app.hh.exceptions import HHError
from app.hh.oauth import HHAuthFlow
from app.hh.schemas import HHNewNegotiationPayload, HHWebhookEvent
from app.logger import logger
from app.services.hh import HHService
from app.services.screening import register_response


def _page(text: str, status: int = 200) -> web.Response:
    return web.Response(
        text=f"<!doctype html><meta charset='utf-8'><p>{escape(text)}</p>",
        content_type="text/html",
        status=status,
    )


async def _notify(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError:
        logger.exception("Не удалось уведомить %s о подключении hh.ru", chat_id)


def create_web_app(
    *,
    bot: Bot,
    hh: HHClient,
    auth_flow: HHAuthFlow,
    session_factory: async_sessionmaker[AsyncSession],
    callback_path: str,
    webhook_secret: str,
    new_responses: asyncio.Event,
) -> web.Application:
    async def callback(request: web.Request) -> web.Response:
        pending = auth_flow.pop(request.query.get("state", ""))
        if pending is None:
            return _page("Ссылка устарела. Запросите новую в боте: /connect_hh", status=400)
        code = request.query.get("code")
        if code is None:
            await _notify(bot, pending.hr_tg_id, "Доступ к hh.ru не выдан.")
            return _page("Доступ не выдан. Можно закрыть страницу.")

        try:
            token = await hh.exchange_code(code, pending.code_verifier)
        except HHError:
            logger.exception("Не удалось получить токены hh.ru: %s", pending.hr_tg_id)
            await _notify(bot, pending.hr_tg_id, "Не получилось подключить hh.ru: /connect_hh")
            return _page("Не получилось подключить hh.ru. Попробуйте ещё раз.", status=502)

        async with session_factory() as session:
            await HHService(session, hh).save_tokens(pending.hr_tg_id, token)
            await session.commit()
        logger.info("hh.ru подключён: %s", pending.hr_tg_id)
        await _notify(bot, pending.hr_tg_id, "hh.ru подключён. Можно присылать отклики в /new.")
        return _page("hh.ru подключён, можно вернуться в Telegram.")

    async def webhook(request: web.Request) -> web.Response:
        if not hmac.compare_digest(request.query.get("secret", ""), webhook_secret):
            logger.warning("Вебхук hh.ru отклонён: неверный секрет")
            return web.Response(status=403)
        try:
            event = HHWebhookEvent.model_validate(await request.json())
        except (ValueError, ValidationError):
            return web.Response(status=400)
        if event.action_type != NEW_NEGOTIATION_ACTION:
            return web.Response(status=200)
        try:
            payload = HHNewNegotiationPayload.model_validate(event.payload)
        except ValidationError:
            return web.Response(status=400)

        async with session_factory() as session:
            created = await register_response(session, payload.topic_id, payload.vacancy_id)
            await session.commit()
        if created:
            new_responses.set()
        return web.Response(status=200)

    app = web.Application()
    app.router.add_get(callback_path, callback)
    app.router.add_post(WEBHOOK_PATH, webhook)
    return app
