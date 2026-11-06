import logging
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from app.hh import router as hh_router
from app.hh.client import HHClient
from app.hh.exceptions import HHTransportError
from app.hh.oauth import HHAuthFlow
from app.hh.web import create_web_app
from app.logger import logger
from app.services.hh import HHService
from app.vacancy import router as vacancy_router

MARKER = "SECRET_RESUME_TEXT"


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.answers: list[str] = []
        self.from_user = SimpleNamespace(id=7)
        self.chat = SimpleNamespace(id=7)

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)


async def test_vacancy_link_error_reply_and_log_hide_text(session, monkeypatch, caplog):
    async def boom(self, hr_tg_id, vacancy_id):
        raise HHTransportError(MARKER)

    monkeypatch.setattr(HHService, "get_vacancy", boom)
    logger.addHandler(caplog.handler)
    message = FakeMessage("https://hh.ru/vacancy/123")
    hh = HHClient("http://x", "agent", "cid", "secret", "https://x/cb")
    try:
        await vacancy_router.vacancy_link(message, session, None, hh)
    finally:
        logger.removeHandler(caplog.handler)
        await hh.close()
    assert message.answers
    assert MARKER not in "".join(message.answers)
    assert MARKER not in caplog.text


async def test_start_tracking_error_reply_and_log_hide_text(session, vacancy, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise HHTransportError(MARKER)

    monkeypatch.setattr(hh_router, "track_vacancy", boom)
    monkeypatch.setattr(hh_router, "webhook_url", lambda *a: "https://x/hook")
    logger.addHandler(caplog.handler)
    message = FakeMessage()
    try:
        assert await hh_router.start_tracking(message, session, None, vacancy, "123") is False
    finally:
        logger.removeHandler(caplog.handler)
    assert message.answers
    assert MARKER not in "".join(message.answers)
    assert MARKER not in caplog.text


async def test_oauth_callback_failure_log_hides_text(caplog):
    class FailingHH:
        async def exchange_code(self, code, verifier):
            raise HHTransportError(MARKER)

    class Bot:
        sent: list[str] = []

        async def send_message(self, chat_id, text):
            self.sent.append(text)

    auth_flow = HHAuthFlow.__new__(HHAuthFlow)
    auth_flow.pop = lambda state: SimpleNamespace(hr_tg_id=7, code_verifier="v")
    bot = Bot()
    app = create_web_app(
        bot=bot,
        hh=FailingHH(),
        auth_flow=auth_flow,
        session_factory=None,
        callback_path="/cb",
        webhook_secret="s",
        new_responses=None,
    )
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=logger.name)
    try:
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/cb?state=a&code=c")
            body = await resp.text()
    finally:
        logger.removeHandler(caplog.handler)
    assert resp.status == 502
    assert MARKER not in body + "".join(bot.sent)
    assert "HHTransportError" in caplog.text
    assert MARKER not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
