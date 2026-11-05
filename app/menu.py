from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.hh.oauth import HHAuthFlow
from app.hh.router import connect_hh, pick_vacancy
from app.interview.router import new_interview
from app.keyboards import CONNECT, INTERVIEW, RESPONSES, TRACK, VACANCY
from app.settings import settings
from app.vacancy.router import new_vacancy

# Подключается первым: текст кнопки не должен попасть в обработчики шагов и ответов
menu_router = Router(name="menu")
menu_router.message.filter(
    F.from_user.id.in_(settings.HR_IDS), F.text.in_({VACANCY, INTERVIEW, TRACK, RESPONSES, CONNECT})
)


@menu_router.message()
async def on_menu_button(
    message: Message, session: AsyncSession, state: FSMContext, hh_auth: HHAuthFlow | None
) -> None:
    await state.clear()
    match message.text:
        case "Создать вакансию":
            await new_vacancy(message, state)
        case "Создать интервью":
            await new_interview(message, session, state)
        case "Отслеживать hh":
            await pick_vacancy(message, CommandObject(command="track"), session, state)
        case "Отклики":
            await pick_vacancy(message, CommandObject(command="responses"), session, state)
        case "Подключить hh":
            await connect_hh(message, hh_auth)
