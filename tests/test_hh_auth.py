import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.hh.client import HHClient
from app.hh.dao import HHTokenDAO
from app.hh.exceptions import HHNotConnected
from app.hh.oauth import HHAuthFlow
from app.hh.schemas import HHResume, HHTokenResponse
from app.hh.web import create_web_app
from app.services.hh import HHService


class FakeHH(HHClient):
    def __init__(self):
        super().__init__("https://api.hh.ru", "test", "cid", "secret", "https://x/hh/callback")
        self.refreshed_with: list[str] = []
        self.resume_token: str | None = None

    async def exchange_code(self, code: str, code_verifier: str) -> HHTokenResponse:
        assert code == "CODE"
        return HHTokenResponse(access_token="A1", refresh_token="R1", expires_in=3600)

    async def refresh_token(self, refresh_token: str) -> HHTokenResponse:
        self.refreshed_with.append(refresh_token)
        return HHTokenResponse(access_token="A2", refresh_token="R2", expires_in=3600)

    async def get_negotiation_resume(self, access_token: str, negotiation_id: str) -> HHResume:
        self.resume_token = access_token
        return HHResume(first_name="Иван")


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


async def test_not_connected_manager(session):
    with pytest.raises(HHNotConnected):
        await HHService(session, FakeHH()).get_negotiation_resume(7, "123")


async def test_valid_token_is_used_without_refresh(session):
    hh = FakeHH()
    service = HHService(session, hh)
    await service.save_tokens(
        7, HHTokenResponse(access_token="A1", refresh_token="R1", expires_in=60)
    )
    await service.get_negotiation_resume(7, "123")
    assert hh.resume_token == "A1"
    assert hh.refreshed_with == []


async def test_expired_token_is_refreshed_and_saved(session):
    hh = FakeHH()
    service = HHService(session, hh)
    await service.save_tokens(
        7, HHTokenResponse(access_token="A1", refresh_token="R1", expires_in=60)
    )
    row = await HHTokenDAO(session).get(7)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    await service.get_negotiation_resume(7, "123")
    assert hh.refreshed_with == ["R1"]
    assert hh.resume_token == "A2"
    assert (row.access_token, row.refresh_token) == ("A2", "R2")


@pytest.fixture
async def callback_client(session):
    hh, bot = FakeHH(), FakeBot()
    flow = HHAuthFlow("cid", "https://x/hh/callback")
    app = create_web_app(
        bot=bot,
        hh=hh,
        auth_flow=flow,
        session_factory=async_sessionmaker(session.bind, expire_on_commit=False),
        callback_path="/hh/callback",
        webhook_secret="s3cret",
        new_responses=asyncio.Event(),
    )
    async with TestClient(TestServer(app)) as client:
        yield client, flow, bot


async def test_callback_saves_tokens_and_notifies_manager(session, callback_client):
    client, flow, bot = callback_client
    state = parse_qs(urlparse(flow.start(hr_tg_id=7)).query)["state"][0]

    response = await client.get("/hh/callback", params={"code": "CODE", "state": state})
    assert response.status == 200
    row = await HHTokenDAO(session).get(7)
    assert row.access_token == "A1"
    assert bot.sent[0][0] == 7

    response = await client.get("/hh/callback", params={"code": "CODE", "state": state})
    assert response.status == 400


async def test_callback_with_denied_access(session, callback_client):
    client, flow, bot = callback_client
    state = parse_qs(urlparse(flow.start(hr_tg_id=7)).query)["state"][0]

    response = await client.get("/hh/callback", params={"error": "access_denied", "state": state})
    assert response.status == 200
    assert await HHTokenDAO(session).get(7) is None
    assert bot.sent == [(7, "Доступ к hh.ru не выдан.")]
