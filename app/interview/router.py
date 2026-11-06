import uuid
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.deep_linking import create_start_link
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import InterviewStatus
from app.files import TxtFileError, read_txt_document
from app.hh.client import HHClient, parse_negotiation_id
from app.hh.exceptions import HHError, HHNotConnected
from app.interview.dao import InterviewDAO
from app.interview.models import Interview, InterviewQuestion
from app.keyboards import MENU_KEYBOARD
from app.llm.client import LLMClient
from app.llm.exceptions import LLMError
from app.logger import logger
from app.services.hh import HHService
from app.services.interview import InterviewService
from app.services.report import render_created, render_html, render_summary
from app.settings import settings
from app.vacancy.dao import VacancyDAO

MIN_RESUME_CHARS = 300

hr_router = Router(name="hr")
hr_router.message.filter(F.from_user.id.in_(settings.HR_IDS))
hr_router.callback_query.filter(F.from_user.id.in_(settings.HR_IDS))

candidate_router = Router(name="candidate")

_busy: set[int] = set()


class NewInterview(StatesGroup):
    resume = State()


class VacancyCallback(CallbackData, prefix="vacancy"):
    vacancy_id: int


@hr_router.message(CommandStart())
@hr_router.message(Command("help"))
async def hr_help(message: Message) -> None:
    await message.answer(
        "/connect_hh — подключить аккаунт работодателя hh.ru.\n"
        "/vacancy — добавить вакансию: бот предложит критерии и обязательные вопросы.\n"
        "/new — создать интервью: выбрать вакансию и прислать отклик с hh.ru.\n"
        "/track — отслеживать новые отклики на вакансию hh.ru.\n"
        "/responses — рейтинг откликов по вакансии.\n\n"
        "Бот проанализирует резюме, подготовит вопросы и даст ссылку для кандидата. "
        "Когда кандидат закончит, сюда придёт отчёт. По отслеживаемым вакансиям бот сам "
        "разбирает новые отклики и присылает их с оценкой резюме.",
        reply_markup=MENU_KEYBOARD,
    )


@hr_router.message(Command("new"))
async def new_interview(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    vacancies = await VacancyDAO(session).find_all()
    if not vacancies:
        await message.answer("Вакансий пока нет. Добавьте первую: /vacancy")
        return
    builder = InlineKeyboardBuilder()
    for vacancy in vacancies:
        builder.button(text=vacancy.title, callback_data=VacancyCallback(vacancy_id=vacancy.id))
    builder.adjust(1)
    await message.answer("Выберите вакансию:", reply_markup=builder.as_markup())


@hr_router.callback_query(VacancyCallback.filter())
async def choose_vacancy(
    callback: CallbackQuery, callback_data: VacancyCallback, state: FSMContext
) -> None:
    await state.set_state(NewInterview.resume)
    await state.update_data(vacancy_id=callback_data.vacancy_id)
    await callback.message.answer(
        "Пришлите ссылку на резюме из отклика hh.ru (hh.ru/resume/…?t=…) или ID отклика.\n"
        "Для теста без доступа к hh можно прислать резюме файлом .txt."
    )
    await callback.answer()


@hr_router.message(NewInterview.resume, F.document)
async def resume_file(
    message: Message, bot: Bot, session: AsyncSession, state: FSMContext, llm: LLMClient
) -> None:
    try:
        text = await read_txt_document(bot, message.document)
    except TxtFileError as exc:
        await message.answer(str(exc))
        return
    if len(text) < MIN_RESUME_CHARS:
        await message.answer("В файле слишком мало текста для резюме.")
        return
    await _create_interview(message, session, state, llm, text, None)


@hr_router.message(NewInterview.resume, F.text)
async def resume_link(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    llm: LLMClient,
    hh: HHClient | None,
) -> None:
    negotiation_id = parse_negotiation_id(message.text)
    if negotiation_id is None:
        await message.answer(
            "Пришлите ссылку на резюме из отклика hh.ru, ID отклика или файл .txt с резюме. "
            "Длинный текст в сообщении Telegram разбивает на части."
        )
        return

    if hh is None:
        await message.answer("Интеграция с hh.ru не настроена: нет приложения hh в .env.")
        return
    try:
        resume = await HHService(session, hh).get_negotiation_resume(
            message.from_user.id, negotiation_id
        )
    except HHNotConnected:
        await message.answer("Сначала подключите аккаунт работодателя hh.ru: /connect_hh")
        return
    except HHError as exc:
        logger.warning(
            "Ошибка запроса к hh.ru по отклику %s: %s", negotiation_id, type(exc).__name__
        )
        await message.answer(
            "Не получилось забрать резюме из hh.ru.\n"
            "Если доступ истёк или отозван, переподключите hh.ru: /connect_hh"
        )
        return
    await _create_interview(message, session, state, llm, resume.to_text(), negotiation_id)


async def _create_interview(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    llm: LLMClient,
    resume: str,
    hh_negotiation_id: str | None,
) -> None:
    data = await state.get_data()
    vacancy = await VacancyDAO(session).get_with_profile(data["vacancy_id"])
    if vacancy is None:
        await state.clear()
        await message.answer("Вакансия не найдена, начните заново: /new")
        return

    await message.answer("Анализирую резюме и готовлю вопросы, это займёт до минуты…")
    try:
        interview, questions = await InterviewService(session, llm).create(
            vacancy, message.from_user.id, resume, hh_negotiation_id
        )
    except LLMError as exc:
        logger.error("Ошибка анализа резюме: %s", type(exc).__name__)
        await message.answer("LLM не ответила. Пришлите резюме ещё раз.")
        return
    await state.clear()
    link = await create_start_link(message.bot, interview.id.hex)
    await message.answer(render_created(interview, vacancy, len(questions), link))


@candidate_router.message(CommandStart(deep_link=True))
async def start_interview(
    message: Message, command: CommandObject, session: AsyncSession, llm: LLMClient
) -> None:
    try:
        interview_id = uuid.UUID(command.args)
    except ValueError:
        await message.answer("Ссылка недействительна.")
        return
    interview = await InterviewDAO(session).get_full(interview_id)
    if interview is None:
        await message.answer("Ссылка недействительна.")
        return
    if interview.candidate_tg_id not in (None, message.from_user.id):
        await message.answer("Это интервью уже проходит другой пользователь.")
        return
    if interview.status is InterviewStatus.FINISHED:
        await message.answer("Интервью уже завершено, спасибо!")
        return

    is_new = interview.status is InterviewStatus.CREATED
    question = await InterviewService(session, llm).start(interview, message.from_user.id)
    if is_new:
        await message.answer(
            f"Здравствуйте! Это техническое интервью на вакансию "
            f"«{escape(interview.vacancy.title)}». Будет {len(interview.questions)} вопросов, "
            f"иногда я задам уточняющий.\n\n"
            f"Отвечайте текстом, по возможности на конкретных примерах из вашего опыта."
        )
    await message.answer(_format_question(interview, question))


@candidate_router.message(CommandStart(), ~F.from_user.id.in_(settings.HR_IDS))
async def candidate_help(message: Message) -> None:
    await message.answer("Здравствуйте! Ссылку на интервью вам пришлёт рекрутер.")


@candidate_router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_answer(message: Message, bot: Bot, session: AsyncSession, llm: LLMClient) -> None:
    user_id = message.from_user.id
    if user_id in _busy:
        await message.answer("Секунду, ещё обрабатываю предыдущий ответ.")
        return
    _busy.add(user_id)
    try:
        await _process_answer(message, bot, session, llm)
    finally:
        _busy.discard(user_id)


async def _process_answer(
    message: Message, bot: Bot, session: AsyncSession, llm: LLMClient
) -> None:
    interview = await InterviewDAO(session).find_active_for_candidate(message.from_user.id)
    if interview is None:
        await message.answer("Активного интервью нет. Откройте ссылку от рекрутера.")
        return

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        question = await InterviewService(session, llm).answer(interview, message.text)
    except LLMError as exc:
        logger.error("Ошибка обработки ответа, интервью %s: %s", interview.id, type(exc).__name__)
        await message.answer("Не получилось обработать ответ. Отправьте его ещё раз.")
        return

    if question is not None:
        await message.answer(_format_question(interview, question))
        return
    await message.answer("Спасибо! Интервью завершено, результаты передадим рекрутеру.")
    await _send_report(bot, interview)


@candidate_router.message(StateFilter(None), ~F.text)
async def on_non_text(message: Message, session: AsyncSession) -> None:
    interview = await InterviewDAO(session).find_active_for_candidate(message.from_user.id)
    if interview is not None:
        await message.answer("Пожалуйста, ответьте на вопрос текстом.")


def _format_question(interview: Interview, question: InterviewQuestion) -> str:
    if question.follow_up is not None:
        return f"<b>Уточнение</b>\n{escape(question.follow_up)}"
    return (
        f"<b>Вопрос {question.position + 1}/{len(interview.questions)}</b>\n{escape(question.text)}"
    )


async def _send_report(bot: Bot, interview: Interview) -> None:
    report = BufferedInputFile(
        render_html(interview).encode(), filename=f"interview_{interview.id.hex[:8]}.html"
    )
    try:
        await bot.send_message(interview.hr_tg_id, render_summary(interview))
        await bot.send_document(interview.hr_tg_id, report)
        logger.info("Отчёт отправлен: %s", interview.id)
    except TelegramAPIError as exc:
        logger.error(
            "Не удалось отправить отчёт по интервью %s: %s", interview.id, type(exc).__name__
        )
