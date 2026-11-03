from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import QuestionMode, QuestionSource
from app.files import TxtFileError, read_txt_document
from app.hh.client import HHClient, parse_vacancy_id
from app.hh.exceptions import HHError, HHNotConnected
from app.hh.router import start_tracking
from app.llm.client import LLMClient
from app.llm.exceptions import LLMError
from app.llm.schemas import VacancyProfile
from app.logger import logger
from app.services.hh import HHService
from app.services.vacancy import (
    MAX_POOL_QUESTIONS,
    QuestionsDraft,
    VacancyService,
    assemble_questions,
    parse_question_pool,
)
from app.settings import settings

MIN_VACANCY_CHARS = 200

MODE_LABELS = {
    QuestionMode.GENERATE: "Сгенерировать",
    QuestionMode.COMPANY: "Только свои",
    QuestionMode.COMPANY_PLUS: "Свои + дополнить",
}
SOURCE_MARKS = {QuestionSource.COMPANY: "компания", QuestionSource.GENERATED: "сгенерирован"}

router = Router(name="vacancy")
router.message.filter(F.from_user.id.in_(settings.HR_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.HR_IDS))


class NewVacancy(StatesGroup):
    source = State()
    mode = State()
    pool = State()
    review = State()


class ModeCallback(CallbackData, prefix="qmode"):
    mode: QuestionMode


class DraftCallback(CallbackData, prefix="draft"):
    action: str


@router.message(Command("vacancy"))
async def new_vacancy(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewVacancy.source)
    await message.answer(
        "Пришлите ссылку на вакансию hh.ru или файл .txt с текстом вакансии. "
        "Я предложу критерии оценки и обязательные вопросы для интервью."
    )


@router.message(NewVacancy.source, F.document)
async def vacancy_file(message: Message, bot: Bot, state: FSMContext) -> None:
    try:
        text = await read_txt_document(bot, message.document)
    except TxtFileError as exc:
        await message.answer(str(exc))
        return
    if len(text) < MIN_VACANCY_CHARS:
        await message.answer("В файле слишком мало текста для вакансии.")
        return
    await _ask_mode(message, state, text)


@router.message(NewVacancy.source, F.text)
async def vacancy_link(
    message: Message, session: AsyncSession, state: FSMContext, hh: HHClient | None
) -> None:
    vacancy_id = parse_vacancy_id(message.text)
    if vacancy_id is None:
        await message.answer(
            "Пришлите ссылку на вакансию hh.ru или файл .txt с текстом вакансии. "
            "Длинный текст в сообщении Telegram разбивает на части."
        )
        return

    if hh is None:
        await message.answer(
            "Интеграция с hh.ru не настроена, поэтому по ссылке вакансию не открыть. "
            "Пришлите текст вакансии файлом .txt."
        )
        return
    try:
        vacancy = await HHService(session, hh).get_vacancy(message.from_user.id, vacancy_id)
    except HHNotConnected:
        await message.answer("Сначала подключите аккаунт работодателя hh.ru: /connect_hh")
        return
    except HHError as exc:
        logger.warning("hh.ru request failed for vacancy %s: %s", vacancy_id, exc)
        await message.answer(f"Не получилось забрать вакансию из hh.ru: {escape(str(exc))}")
        return
    await _ask_mode(message, state, vacancy.to_text(), vacancy_id)


async def _ask_mode(
    message: Message, state: FSMContext, vacancy_text: str, hh_vacancy_id: str | None = None
) -> None:
    await state.set_state(NewVacancy.mode)
    await state.update_data(vacancy_text=vacancy_text, pool=[], hh_vacancy_id=hh_vacancy_id)
    builder = InlineKeyboardBuilder()
    for mode, label in MODE_LABELS.items():
        builder.button(text=label, callback_data=ModeCallback(mode=mode))
    builder.adjust(1)
    await message.answer(
        "Откуда брать обязательные вопросы?\n"
        "• <b>Сгенерировать</b> — вопросы составит LLM по вакансии.\n"
        "• <b>Только свои</b> — заготовленные вопросы компании, без добавок.\n"
        "• <b>Свои + дополнить</b> — вопросы компании, а к ключевым критериям без вопросов "
        "LLM добавит свои.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(NewVacancy.mode, ModeCallback.filter())
async def choose_mode(
    callback: CallbackQuery, callback_data: ModeCallback, state: FSMContext, llm: LLMClient
) -> None:
    await state.update_data(mode=callback_data.mode)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    if callback_data.mode is QuestionMode.GENERATE:
        await _draft(callback.message, state, llm)
        return
    await state.set_state(NewVacancy.pool)
    await callback.message.answer(
        "Пришлите вопросы компании, по одному на строку. Можно несколькими сообщениями "
        "или файлом .txt. Когда закончите, нажмите «Готово»."
    )


@router.message(NewVacancy.pool, F.document)
async def pool_file(message: Message, bot: Bot, state: FSMContext) -> None:
    try:
        text = await read_txt_document(bot, message.document)
    except TxtFileError as exc:
        await message.answer(str(exc))
        return
    await _add_to_pool(message, state, text)


@router.message(NewVacancy.pool, F.text)
async def pool_text(message: Message, state: FSMContext) -> None:
    await _add_to_pool(message, state, message.text)


async def _add_to_pool(message: Message, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    pool = data["pool"] + parse_question_pool(text)
    if len(pool) > MAX_POOL_QUESTIONS:
        await message.answer(
            f"Вопросов больше {MAX_POOL_QUESTIONS}. Оставьте те, что нужны для этой вакансии."
        )
        return
    await state.update_data(pool=pool)
    builder = InlineKeyboardBuilder()
    builder.button(text="Готово", callback_data=DraftCallback(action="pool_done"))
    await message.answer(
        f"Вопросов в пуле: {len(pool)}. Пришлите ещё или нажмите «Готово».",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(NewVacancy.pool, DraftCallback.filter(F.action == "pool_done"))
async def pool_done(callback: CallbackQuery, state: FSMContext, llm: LLMClient) -> None:
    data = await state.get_data()
    if not data["pool"]:
        await callback.answer("Сначала пришлите вопросы", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _draft(callback.message, state, llm)


@router.message(NewVacancy.review, F.text)
async def vacancy_remarks(message: Message, state: FSMContext, llm: LLMClient) -> None:
    data = await state.get_data()
    previous = VacancyProfile.model_validate(data["profile"])
    await _draft(message, state, llm, previous, message.text)


async def _draft(
    message: Message,
    state: FSMContext,
    llm: LLMClient,
    previous: VacancyProfile | None = None,
    remarks: str | None = None,
) -> None:
    data = await state.get_data()
    pool, mode = data["pool"], QuestionMode(data["mode"])
    await message.answer("Составляю профиль вакансии, это займёт до минуты…")
    try:
        profile = await llm.generate_vacancy_profile(
            data["vacancy_text"], pool, mode, previous, remarks
        )
    except LLMError:
        logger.exception("Vacancy profile generation failed")
        await message.answer("LLM не ответила. Отправьте сообщение ещё раз.")
        return
    await state.set_state(NewVacancy.review)
    await state.update_data(profile=profile.model_dump())
    draft = assemble_questions(profile, pool, mode)
    await message.answer(
        f"{_render_draft(profile, draft)}\n\nЕсли нужно что-то поменять, напишите замечания.",
        reply_markup=_review_keyboard(),
    )


def _review_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сохранить", callback_data=DraftCallback(action="save"))
    builder.button(text="Отмена", callback_data=DraftCallback(action="cancel"))
    return builder.as_markup()


@router.callback_query(NewVacancy.review, DraftCallback.filter(F.action == "save"))
async def save_vacancy(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, hh: HHClient | None
) -> None:
    data = await state.get_data()
    profile = VacancyProfile.model_validate(data["profile"])
    draft = assemble_questions(profile, data["pool"], QuestionMode(data["mode"]))
    if not draft.questions:
        await callback.answer(
            "В профиле нет ни одного обязательного вопроса. Напишите замечания.", show_alert=True
        )
        return
    vacancy = await VacancyService(session).create(
        data["vacancy_text"], profile, draft.questions, callback.from_user.id
    )
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Вакансия «{escape(profile.title)}» сохранена. Создать интервью: /new"
    )
    await callback.answer()
    if hh is not None and data.get("hh_vacancy_id"):
        await start_tracking(callback.message, session, hh, vacancy, data["hh_vacancy_id"])


@router.callback_query(NewVacancy.review, DraftCallback.filter(F.action == "cancel"))
async def cancel_vacancy(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Черновик отменён.")
    await callback.answer()


@router.callback_query(DraftCallback.filter())
@router.callback_query(ModeCallback.filter())
async def stale_draft(callback: CallbackQuery) -> None:
    await callback.answer("Этот шаг уже неактуален", show_alert=True)


def _render_draft(profile: VacancyProfile, draft: QuestionsDraft) -> str:
    lines = [f"<b>{escape(profile.title)}</b>", escape(profile.summary), "", "<b>Критерии</b>"]
    for number, c in enumerate(profile.criteria, start=1):
        must = ", must-have" if c.is_must_have else ""
        lines.append(f"{number}. {escape(c.title)} — вес {c.weight}{must}")
        lines.append(f"<i>{escape(c.description)}</i>")
    lines += ["", "<b>Обязательные вопросы</b>"]
    lines += [
        f"• [{q.criterion_number}] {escape(q.text)} <i>({SOURCE_MARKS[q.source]})</i>"
        for q in draft.questions
    ]
    if draft.unassigned:
        lines += ["", "<b>Вопросы компании без критерия, в интервью не попадут</b>"]
        lines += [f"• {escape(q)}" for q in draft.unassigned]
    return "\n".join(lines)
