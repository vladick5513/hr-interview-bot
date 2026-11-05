from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import func, select
from test_interview_service import FakeLLM, _evaluation

from app.hh.router import router as hh_router
from app.interview.dao import InterviewDAO
from app.interview.models import Interview
from app.interview.router import NewInterview, candidate_router, hr_router
from app.keyboards import MENU_KEYBOARD
from app.menu import menu_router
from app.services.interview import InterviewService
from app.vacancy.dao import VacancyDAO
from app.vacancy.router import NewVacancy
from app.vacancy.router import router as vacancy_router

HR, CANDIDATE = 1, 2

dp = Dispatcher()
dp.include_routers(menu_router, candidate_router, hr_router, vacancy_router, hh_router)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        return Message(
            message_id=len(self.sent),
            date=datetime.now(UTC),
            chat=Chat(id=method.chat_id, type="private"),
        )

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError

    async def close(self):
        pass


@pytest.fixture
def bot():
    return Bot("1:AAA", session=FakeSession())


async def send(bot, session, user_id, text, llm=None):
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="x"),
        text=text,
    )
    await dp.feed_update(
        bot, Update(update_id=1, message=message), session=session, llm=llm, hh=None, hh_auth=None
    )
    return bot.session.sent


def state_of(bot, user_id):
    from aiogram.fsm.context import FSMContext

    return FSMContext(dp.fsm.storage, StorageKey(bot.id, user_id, user_id))


async def test_buttons_equal_commands(bot, session, vacancy):
    pairs = [
        ("Создать вакансию", "/vacancy"),
        ("Создать интервью", "/new"),
        ("Отслеживать hh", "/track"),
        ("Отклики", "/responses"),
        ("Подключить hh", "/connect_hh"),
    ]
    for button, command in pairs:
        by_button = (await send(bot, session, HR, button))[-1]
        by_command = (await send(bot, session, HR, command))[-1]
        assert by_button.text == by_command.text
        assert by_button.reply_markup == by_command.reply_markup


async def test_button_resets_state_and_is_not_an_answer(bot, session):
    for step in (NewVacancy.source, NewVacancy.pool, NewVacancy.review):
        await state_of(bot, HR).set_state(step)
        sent = await send(bot, session, HR, "Создать вакансию")
        assert sent[-1].text.startswith("Пришлите ссылку на вакансию")
        assert await state_of(bot, HR).get_state() == NewVacancy.source.state


async def _start_interview(session, llm, candidate_id):
    vacancy = await VacancyDAO(session).get_with_profile(1)
    interview, _ = await InterviewService(session, llm).create(vacancy, HR, "резюме", None)
    await session.commit()
    interview = await InterviewDAO(session).get_full(interview.id)
    await InterviewService(session, llm).start(interview, candidate_id)
    await session.commit()
    return interview.id


async def _first_answer(session, interview_id):
    interview = await InterviewDAO(session).get_full(interview_id)
    return interview.questions[0].answer


async def test_button_with_active_interview_is_not_an_answer(bot, session, vacancy):
    llm = FakeLLM([])
    interview_id = await _start_interview(session, llm, HR)
    sent = await send(bot, session, HR, "Создать вакансию", llm)
    assert sent[-1].text.startswith("Пришлите ссылку на вакансию")
    assert llm.calls == []
    assert await _first_answer(session, interview_id) is None


async def test_non_hr_does_not_get_menu(bot, session, vacancy):
    llm = FakeLLM([_evaluation(3)])
    interview_id = await _start_interview(session, llm, CANDIDATE)
    await send(bot, session, CANDIDATE, "Создать вакансию", llm)
    assert await _first_answer(session, interview_id) == "Создать вакансию"
    assert await state_of(bot, CANDIDATE).get_state() is None

    sent = await send(bot, session, CANDIDATE, "/start")
    assert sent[-1].reply_markup is None
    sent = await send(bot, session, HR, "/start")
    assert sent[-1].reply_markup == MENU_KEYBOARD


async def test_button_on_resume_step_creates_no_interview(bot, session, vacancy):
    state = state_of(bot, HR)
    await state.set_state(NewInterview.resume)
    await state.update_data(vacancy_id=1)
    sent = await send(bot, session, HR, "Отклики")
    assert sent[-1].text == "Выберите вакансию:"
    assert await state.get_state() is None
    assert await session.scalar(select(func.count()).select_from(Interview)) == 0
