from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User

from app.hh.router import router as hh_router
from app.interview import router as interview_module
from app.interview.router import candidate_router, hr_router
from app.keyboards import MENU_KEYBOARD
from app.menu import menu_router
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


async def send(bot, session, user_id, text):
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="x"),
        text=text,
    )
    await dp.feed_update(
        bot, Update(update_id=1, message=message), session=session, llm=None, hh=None, hh_auth=None
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


async def test_button_with_active_interview_is_not_an_answer(bot, session, monkeypatch):
    answers = []

    async def fake_process(message, *args):
        answers.append(message.text)

    monkeypatch.setattr(interview_module, "_process_answer", fake_process)
    await state_of(bot, HR).clear()
    sent = await send(bot, session, HR, "Создать вакансию")
    assert sent[-1].text.startswith("Пришлите ссылку на вакансию")
    assert answers == []


async def test_non_hr_does_not_get_menu(bot, session, monkeypatch):
    answers = []

    async def fake_process(message, *args):
        answers.append(message.text)

    monkeypatch.setattr(interview_module, "_process_answer", fake_process)
    await send(bot, session, CANDIDATE, "Создать вакансию")
    assert answers == ["Создать вакансию"]
    assert await state_of(bot, CANDIDATE).get_state() is None

    sent = await send(bot, session, CANDIDATE, "/start")
    assert sent[-1].reply_markup is None
    sent = await send(bot, session, HR, "/start")
    assert sent[-1].reply_markup == MENU_KEYBOARD
