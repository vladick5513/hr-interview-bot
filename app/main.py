import asyncio
import contextlib
from contextlib import AsyncExitStack
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from app.database import DbSessionMiddleware, session_factory
from app.hh.client import HH_API_URL, HHClient, webhook_secret
from app.hh.oauth import HHAuthFlow
from app.hh.router import router as hh_router
from app.hh.web import create_web_app
from app.hh.worker import screening_worker
from app.interview.router import candidate_router, hr_router
from app.llm.client import LLMClient
from app.logger import logger
from app.menu import menu_router
from app.settings import settings
from app.vacancy.router import router as vacancy_router

WEB_PORT = 8080


async def main() -> None:
    bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.update.middleware(DbSessionMiddleware())
    # Кандидатский роутер первым: deep-link /start должен сработать и у рекрутера
    dp.include_routers(menu_router, candidate_router, hr_router, vacancy_router, hh_router)

    llm = LLMClient(settings.OPENAI_API_KEY, settings.OPENAI_MODEL, settings.OPENAI_BASE_URL)

    async with AsyncExitStack() as stack:
        hh = hh_auth = None
        if settings.hh_enabled:
            hh = HHClient(
                HH_API_URL,
                settings.HH_USER_AGENT,
                settings.HH_CLIENT_ID,
                settings.HH_CLIENT_SECRET,
                settings.HH_REDIRECT_URI,
            )
            stack.push_async_callback(hh.close)
            hh_auth = HHAuthFlow(settings.HH_CLIENT_ID, settings.HH_REDIRECT_URI)
            new_responses = asyncio.Event()

            runner = web.AppRunner(
                create_web_app(
                    bot=bot,
                    hh=hh,
                    auth_flow=hh_auth,
                    session_factory=session_factory,
                    callback_path=urlparse(settings.HH_REDIRECT_URI).path,
                    webhook_secret=webhook_secret(settings.HH_CLIENT_SECRET),
                    new_responses=new_responses,
                )
            )
            await runner.setup()
            stack.push_async_callback(runner.cleanup)
            await web.TCPSite(runner, "0.0.0.0", WEB_PORT).start()
            logger.info("OAuth callback и вебхук hh.ru слушают порт %s", WEB_PORT)

            worker = asyncio.create_task(
                screening_worker(
                    bot=bot,
                    llm=llm,
                    hh=hh,
                    session_factory=session_factory,
                    new_responses=new_responses,
                )
            )
            stack.push_async_callback(_cancel, worker)
        else:
            logger.warning("HH_CLIENT_ID не задан, интеграция с hh.ru выключена")

        logger.info("Бот запущен")
        await dp.start_polling(bot, llm=llm, hh=hh, hh_auth=hh_auth)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


if __name__ == "__main__":
    asyncio.run(main())
