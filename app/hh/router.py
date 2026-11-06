import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.deep_linking import create_start_link
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ResponseStatus
from app.hh.client import HHClient, parse_vacancy_id, webhook_url
from app.hh.dao import HHResponseDAO
from app.hh.exceptions import HHError, HHNotConnected
from app.hh.oauth import HHAuthFlow
from app.llm.client import LLMClient
from app.logger import logger
from app.services.report import render_created, render_ranking
from app.services.screening import ScreeningService, VacancyAlreadyTracked, track_vacancy
from app.settings import settings
from app.vacancy.dao import VacancyDAO
from app.vacancy.models import Vacancy

RANKING_LIMIT = 30
NOT_CONFIGURED = "Интеграция с hh.ru не настроена: нет приложения hh в .env."

router = Router(name="hh")
router.message.filter(F.from_user.id.in_(settings.HR_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.HR_IDS))


class TrackVacancy(StatesGroup):
    link = State()


class VacancyPickCallback(CallbackData, prefix="hhvac"):
    purpose: str
    vacancy_id: int


class ResponseCallback(CallbackData, prefix="resp"):
    action: str
    response_id: int


def response_keyboard(response_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Пригласить на интервью",
        callback_data=ResponseCallback(action="invite", response_id=response_id),
    )
    builder.button(
        text="Отклонить", callback_data=ResponseCallback(action="reject", response_id=response_id)
    )
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("connect_hh"))
async def connect_hh(message: Message, hh_auth: HHAuthFlow | None) -> None:
    if hh_auth is None:
        await message.answer(NOT_CONFIGURED)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="Войти в hh.ru", url=hh_auth.start(message.from_user.id))
    await message.answer(
        "Войдите в hh.ru как работодатель и разрешите доступ. Ссылка действует 10 минут.",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("track"))
@router.message(Command("responses"))
async def pick_vacancy(
    message: Message, command: CommandObject, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    vacancies = await VacancyDAO(session).find_all()
    if not vacancies:
        await message.answer("Вакансий пока нет. Добавьте первую: /vacancy")
        return
    builder = InlineKeyboardBuilder()
    for vacancy in vacancies:
        tracked = " · отслеживается" if vacancy.hh_vacancy_id else ""
        builder.button(
            text=f"{vacancy.title}{tracked}",
            callback_data=VacancyPickCallback(purpose=command.command, vacancy_id=vacancy.id),
        )
    builder.adjust(1)
    await message.answer("Выберите вакансию:", reply_markup=builder.as_markup())


@router.callback_query(VacancyPickCallback.filter(F.purpose == "track"))
async def track_pick(
    callback: CallbackQuery, callback_data: VacancyPickCallback, state: FSMContext
) -> None:
    await state.set_state(TrackVacancy.link)
    await state.update_data(vacancy_id=callback_data.vacancy_id)
    await callback.message.answer(
        "Пришлите ссылку на эту вакансию на hh.ru. Новые отклики на неё бот будет разбирать сам."
    )
    await callback.answer()


@router.message(TrackVacancy.link, F.text)
async def track_link(
    message: Message, session: AsyncSession, state: FSMContext, hh: HHClient | None
) -> None:
    if hh is None:
        await message.answer(NOT_CONFIGURED)
        return
    hh_vacancy_id = parse_vacancy_id(message.text)
    if hh_vacancy_id is None:
        await message.answer("Нужна ссылка вида hh.ru/vacancy/<номер> или номер вакансии.")
        return
    data = await state.get_data()
    vacancy = await VacancyDAO(session).get_with_profile(data["vacancy_id"])
    if vacancy is None:
        await state.clear()
        await message.answer("Вакансия не найдена, начните заново: /track")
        return
    tracked = await start_tracking(message, session, hh, vacancy, hh_vacancy_id)
    if tracked:
        await state.clear()


async def start_tracking(
    message: Message, session: AsyncSession, hh: HHClient, vacancy: Vacancy, hh_vacancy_id: str
) -> bool:
    hr_tg_id = message.chat.id
    url = webhook_url(settings.HH_REDIRECT_URI, settings.HH_CLIENT_SECRET)
    try:
        await track_vacancy(session, hh, vacancy, hr_tg_id, hh_vacancy_id, url)
    except VacancyAlreadyTracked as exc:
        await message.answer(f"Эта вакансия hh уже привязана к «{escape(str(exc))}».")
        return False
    except HHNotConnected:
        await message.answer(
            "Чтобы отслеживать отклики, подключите аккаунт работодателя hh.ru: /connect_hh, "
            "затем /track"
        )
        return False
    except HHError as exc:
        logger.warning(
            "Не удалось подписаться на вебхук hh.ru: %s, %s", hr_tg_id, type(exc).__name__
        )
        await message.answer("Не получилось подписаться на отклики.")
        return False
    await message.answer(
        f"Отслеживаю отклики на «{escape(vacancy.title)}». Новые отклики придут сюда "
        "с оценкой резюме. Рейтинг: /responses"
    )
    return True


@router.callback_query(VacancyPickCallback.filter(F.purpose == "responses"))
async def show_ranking(
    callback: CallbackQuery, callback_data: VacancyPickCallback, session: AsyncSession
) -> None:
    vacancy = await VacancyDAO(session).get_with_profile(callback_data.vacancy_id)
    if vacancy is None:
        await callback.answer("Вакансия не найдена", show_alert=True)
        return
    responses = await HHResponseDAO(session).find_screened(vacancy.id, RANKING_LIMIT)
    await callback.message.answer(render_ranking(vacancy, responses))
    await callback.answer()


@router.callback_query(ResponseCallback.filter(F.action == "invite"))
async def invite_button(
    callback: CallbackQuery,
    callback_data: ResponseCallback,
    session: AsyncSession,
    llm: LLMClient,
    hh: HHClient | None,
) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await _invite(
        callback.message, callback_data.response_id, callback.from_user.id, session, llm, hh
    )
    await callback.answer()


@router.message(F.text.regexp(r"^/invite_(\d+)$").as_("match"))
async def invite_command(
    message: Message,
    match: re.Match[str],
    session: AsyncSession,
    llm: LLMClient,
    hh: HHClient | None,
) -> None:
    await _invite(message, int(match.group(1)), message.from_user.id, session, llm, hh)


async def _invite(
    message: Message,
    response_id: int,
    hr_tg_id: int,
    session: AsyncSession,
    llm: LLMClient,
    hh: HHClient | None,
) -> None:
    response = await HHResponseDAO(session).get(response_id)
    if response is None or response.status is not ResponseStatus.SCREENED:
        await message.answer("Этот отклик уже обработан или не найден.")
        return
    interview, questions, vacancy = await ScreeningService(session, llm, hh).invite(
        response, hr_tg_id
    )
    link = await create_start_link(message.bot, interview.id.hex)
    await message.answer(
        render_created(interview, vacancy, len(questions), link)
        + "\n\nОтправьте ссылку кандидату в чате отклика на hh.ru."
    )


@router.callback_query(ResponseCallback.filter(F.action == "reject"))
async def reject_button(
    callback: CallbackQuery, callback_data: ResponseCallback, session: AsyncSession
) -> None:
    response = await HHResponseDAO(session).get(callback_data.response_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    if response is None or response.status is not ResponseStatus.SCREENED:
        await callback.answer("Этот отклик уже обработан", show_alert=True)
        return
    response.status = ResponseStatus.REJECTED
    await callback.message.answer(
        f"Отклик «{escape(response.candidate_name or '—')}» отклонён. На hh.ru статус не меняется."
    )
    await callback.answer()
