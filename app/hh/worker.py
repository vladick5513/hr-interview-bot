import asyncio
import contextlib

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.enums import ResponseStatus
from app.hh.client import HHClient
from app.hh.dao import HHResponseDAO
from app.hh.router import response_keyboard
from app.llm.client import LLMClient
from app.logger import logger
from app.services.report import render_screened
from app.services.screening import ScreeningService

BATCH_SIZE = 5
IDLE_SECONDS = 60


# Очередь живёт в БД: если процесс упадёт, необработанные отклики останутся со статусом new
# и разберутся после рестарта. Поэтому хватает задачи в event loop бота, без Celery
async def screening_worker(
    *,
    bot: Bot,
    llm: LLMClient,
    hh: HHClient,
    session_factory: async_sessionmaker[AsyncSession],
    new_responses: asyncio.Event,
) -> None:
    while True:
        new_responses.clear()
        try:
            processed = await _screen_batch(bot, llm, hh, session_factory)
        except Exception as exc:
            logger.error("Ошибка при обработке откликов: %s", type(exc).__name__)
            processed = 0
        if processed == BATCH_SIZE:
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(new_responses.wait(), IDLE_SECONDS)


async def _screen_batch(
    bot: Bot,
    llm: LLMClient,
    hh: HHClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        ids = [r.id for r in await HHResponseDAO(session).find_new(BATCH_SIZE)]

    for response_id in ids:
        async with session_factory() as session:
            response = await HHResponseDAO(session).get(response_id)
            if response is None or response.status is not ResponseStatus.NEW:
                continue
            vacancy = await ScreeningService(session, llm, hh).screen(response)
            await session.commit()
        if vacancy is None:
            logger.warning("Отклик %s не оценён: %s", response_id, response.error)
            continue
        try:
            await bot.send_message(
                vacancy.owner_tg_id,
                render_screened(response, vacancy),
                reply_markup=response_keyboard(response.id),
            )
        except TelegramAPIError as exc:
            logger.error(
                "Не удалось отправить уведомление об отклике %s: %s",
                response_id,
                type(exc).__name__,
            )
    return len(ids)
