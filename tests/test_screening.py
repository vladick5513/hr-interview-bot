import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.enums import InterviewStatus, ResponseStatus
from app.hh.client import HHClient, webhook_secret, webhook_url
from app.hh.dao import HHResponseDAO
from app.hh.exceptions import HHAPIError
from app.hh.oauth import HHAuthFlow
from app.hh.schemas import (
    HHResume,
    HHSubscription,
    HHSubscriptionAction,
    HHSubscriptions,
    HHTokenResponse,
)
from app.hh.web import create_web_app
from app.llm.client import LLMClient
from app.llm.exceptions import LLMTransportError
from app.llm.schemas import CriterionMatch, GeneratedQuestion, ResumeAnalysis
from app.services.hh import HHService
from app.services.scoring import resume_score
from app.services.screening import (
    MAX_ATTEMPTS,
    ScreeningService,
    VacancyAlreadyTracked,
    register_response,
    track_vacancy,
)
from app.vacancy.dao import VacancyDAO
from app.vacancy.models import Vacancy, VacancyCriterion

OWNER = 7
SECRET = webhook_secret("secret")


class FakeHH(HHClient):
    def __init__(self, resume_error: Exception | None = None):
        super().__init__("https://api.hh.ru", "test", "cid", "secret", "https://bot.example/cb")
        self.resume_error = resume_error
        self.subscriptions: list[HHSubscription] = []

    async def get_negotiation_resume(self, access_token: str, negotiation_id: str) -> HHResume:
        if self.resume_error is not None:
            raise self.resume_error
        return HHResume(first_name="Иван", last_name="Петров", skills="RAG в проде")

    async def get_subscriptions(self, access_token: str) -> HHSubscriptions:
        return HHSubscriptions(items=self.subscriptions)

    async def subscribe_new_responses(self, access_token: str, url: str) -> None:
        self.subscriptions.append(
            HHSubscription(
                id=str(len(self.subscriptions)),
                url=url,
                actions=[HHSubscriptionAction(type="NEW_NEGOTIATION_VACANCY")],
            )
        )


class FakeLLM(LLMClient):
    def __init__(self, fail: bool = False):
        super().__init__(api_key="test", model="test", base_url=None)
        self.fail = fail
        self.calls = 0

    async def analyze_resume(
        self, vacancy: Vacancy, resume_text: str, questions_count: int
    ) -> ResumeAnalysis:
        self.calls += 1
        if self.fail:
            raise LLMTransportError("timeout")
        return ResumeAnalysis(
            candidate_name="Иван Петров",
            summary="ML-инженер",
            skills=["RAG"],
            total_experience_years=4,
            criteria_matches=[
                CriterionMatch(criterion_id=1, score=4, rationale="RAG в проде"),
                CriterionMatch(criterion_id=2, score=2, rationale="мало Python"),
            ],
            questions=[GeneratedQuestion(criterion_id=1, text="Про ваш RAG", reason="")],
        )


@pytest.fixture
async def tracked(session, vacancy):
    vacancy.hh_vacancy_id = "555"
    vacancy.owner_tg_id = OWNER
    await HHService(session, FakeHH()).save_tokens(
        OWNER, HHTokenResponse(access_token="A", refresh_token="R", expires_in=3600)
    )
    await session.commit()
    return vacancy


async def _queued(session) -> list:
    return await HHResponseDAO(session).find_new(10)


def test_resume_score_is_weighted():
    criteria = [
        VacancyCriterion(id=1, weight=3, is_must_have=True),
        VacancyCriterion(id=2, weight=1, is_must_have=False),
    ]
    assert resume_score(criteria, {1: 4, 2: 2}) == 70.0
    assert resume_score(criteria, {}) is None


async def test_register_skips_untracked_and_duplicates(session, tracked):
    assert await register_response(session, "t1", "555") is True
    assert await register_response(session, "t1", "555") is False
    assert await register_response(session, "t2", "999") is False
    assert await register_response(session, "t3", None) is False
    assert [r.topic_id for r in await _queued(session)] == ["t1"]


async def test_screen_and_invite(session, tracked):
    await register_response(session, "t1", "555")
    (response,) = await _queued(session)
    llm = FakeLLM()
    service = ScreeningService(session, llm, FakeHH())

    vacancy = await service.screen(response)
    assert vacancy.id == tracked.id
    assert response.status is ResponseStatus.SCREENED
    assert response.candidate_name == "Иван Петров"
    assert response.resume_score == 70.0
    assert "RAG в проде" in response.resume_text

    interview, questions, _ = await service.invite(response, OWNER)
    assert llm.calls == 1, "приглашение берёт готовый разбор резюме, без второго вызова LLM"
    assert response.status is ResponseStatus.INVITED
    assert response.interview_id == interview.id
    assert interview.status is InterviewStatus.CREATED
    assert interview.hh_negotiation_id == "t1"
    assert [q.text for q in questions][-1] == "Про ваш RAG"


async def test_temporary_failure_is_retried_then_failed(session, tracked):
    await register_response(session, "t1", "555")
    (response,) = await _queued(session)
    service = ScreeningService(session, FakeLLM(fail=True), FakeHH())

    for attempt in range(1, MAX_ATTEMPTS):
        assert await service.screen(response) is None
        assert response.status is ResponseStatus.NEW
        assert response.attempts == attempt
    assert await service.screen(response) is None
    assert response.status is ResponseStatus.FAILED
    assert response.error == "timeout"


async def test_permanent_hh_error_fails_immediately(session, tracked):
    await register_response(session, "t1", "555")
    (response,) = await _queued(session)
    hh = FakeHH(resume_error=HHAPIError(404, ["not_found"]))
    assert await ScreeningService(session, FakeLLM(), hh).screen(response) is None
    assert response.status is ResponseStatus.FAILED


async def test_track_vacancy_subscribes_once_and_rejects_double_link(session, tracked):
    hh = FakeHH()
    url = webhook_url("https://bot.example/hh/callback", "secret")
    other = Vacancy(title="Другая", description="")
    session.add(other)
    await session.flush()

    await track_vacancy(session, hh, tracked, OWNER, "555", url)
    await track_vacancy(session, hh, tracked, OWNER, "555", url)
    assert [s.url for s in hh.subscriptions] == [url]

    with pytest.raises(VacancyAlreadyTracked):
        await track_vacancy(session, hh, other, OWNER, "555", url)
    assert (await VacancyDAO(session).find_by_hh_id("555")).id == tracked.id


@pytest.fixture
async def webhook_client(session):
    new_responses = asyncio.Event()
    app = create_web_app(
        bot=None,
        hh=FakeHH(),
        auth_flow=HHAuthFlow("cid", "https://bot.example/cb"),
        session_factory=async_sessionmaker(session.bind, expire_on_commit=False),
        callback_path="/hh/callback",
        webhook_secret=SECRET,
        new_responses=new_responses,
    )
    async with TestClient(TestServer(app)) as client:
        yield client, new_responses


def _event(topic_id: str, vacancy_id: str = "555", action: str = "NEW_NEGOTIATION_VACANCY"):
    return {
        "id": f"evt-{topic_id}",
        "subscription_id": "s1",
        "action_type": action,
        "user_id": "0",
        "payload": {
            "topic_id": topic_id,
            "vacancy_id": vacancy_id,
            "resume_id": "r1",
            "chat_id": "c1",
            "employer_id": "e1",
            "negotiation_date": "2026-11-03T10:00:00+0300",
        },
    }


async def test_webhook_queues_response_and_wakes_worker(session, tracked, webhook_client):
    client, new_responses = webhook_client
    response = await client.post(f"/hh/webhook?secret={SECRET}", json=_event("t1"))
    assert response.status == 200
    assert new_responses.is_set()

    new_responses.clear()
    response = await client.post(f"/hh/webhook?secret={SECRET}", json=_event("t1"))
    assert response.status == 200
    assert not new_responses.is_set()
    assert [r.topic_id for r in await _queued(session)] == ["t1"]


async def test_webhook_rejects_wrong_secret(session, tracked, webhook_client):
    client, _ = webhook_client
    response = await client.post("/hh/webhook?secret=wrong", json=_event("t1"))
    assert response.status == 403
    assert await _queued(session) == []


async def test_webhook_ignores_other_actions(session, tracked, webhook_client):
    client, new_responses = webhook_client
    response = await client.post(
        f"/hh/webhook?secret={SECRET}", json=_event("t1", action="CHAT_MESSAGE_CREATED")
    )
    assert response.status == 200
    assert not new_responses.is_set()
    assert await _queued(session) == []


async def test_client_subscription_request():
    seen: dict = {}

    async def subscribe(request: web.Request) -> web.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = await request.json()
        return web.Response(status=201)

    app = web.Application()
    app.router.add_post("/webhook/subscriptions", subscribe)
    async with TestServer(app) as server:
        client = HHClient(str(server.make_url("")), "ua", "cid", "secret", "https://x/cb")
        await client.subscribe_new_responses("A", "https://x/hh/webhook?secret=1")
        await client.close()
    assert seen == {
        "auth": "Bearer A",
        "body": {
            "url": "https://x/hh/webhook?secret=1",
            "actions": [{"type": "NEW_NEGOTIATION_VACANCY"}],
        },
    }
