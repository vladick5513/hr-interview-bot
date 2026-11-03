from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.hh.client import HHClient, parse_negotiation_id
from app.hh.exceptions import HHAPIError, HHUnexpectedResponse
from app.hh.oauth import HHAuthFlow
from app.hh.schemas import HHResume

RESUME = {
    "id": "0123456789abcdef",
    "first_name": "Иван",
    "last_name": "Иванов",
    "middle_name": None,
    "title": "ML Engineer",
    "total_experience": {"months": 62},
    "skill_set": ["Python", "RAG"],
    "skills": "Строю LLM-сервисы",
    "experience": [
        {
            "company": "HeadHunter",
            "position": "ML Engineer",
            "start": "2021-01-01",
            "end": None,
            "description": "Делал матчинг резюме",
            "area": {"id": "1", "name": "Москва"},
        }
    ],
    "education": {
        "level": {"id": "master", "name": "Магистр"},
        "primary": [{"name": "МГУ", "organization": "ВМК", "year": 2018}],
    },
    "language": [{"id": "eng", "name": "Английский", "level": {"id": "b2", "name": "B2"}}],
    "photo": None,
}


@pytest.fixture
async def hh_server():
    seen: dict = {}

    async def negotiation(request: web.Request) -> web.Response:
        seen["headers"] = dict(request.headers)
        nid = request.match_info["nid"]
        if nid == "404":
            return web.json_response({"errors": [{"type": "not_found"}]}, status=404)
        if nid == "deleted":
            return web.json_response({"id": nid, "resume": None})
        if nid == "foreign":
            return web.json_response({"id": nid, "resume": {"url": "https://evil.example/r"}})
        resume_url = request.url.with_path("/resumes/0123456789abcdef").with_query(topic_id=nid)
        return web.json_response({"id": nid, "resume": {"url": str(resume_url)}})

    async def resume(request: web.Request) -> web.Response:
        assert request.query["topic_id"] == "123"
        return web.json_response(RESUME)

    async def token(request: web.Request) -> web.Response:
        form = dict(await request.post())
        seen["token_form"] = form
        if form.get("refresh_token") == "used":
            return web.json_response(
                {"error": "invalid_grant", "error_description": "token not found"}, status=400
            )
        return web.json_response(
            {
                "access_token": "A",
                "refresh_token": "R",
                "expires_in": 1209600,
                "token_type": "bearer",
            }
        )

    app = web.Application()
    app.router.add_get("/negotiations/{nid}", negotiation)
    app.router.add_get("/resumes/{resume_id}", resume)
    app.router.add_post("/token", token)
    async with TestServer(app) as server:
        client = HHClient(str(server.make_url("")), "test-agent", "cid", "secret", "https://x/cb")
        yield client, seen
        await client.close()


async def test_resume_is_fetched_through_negotiation(hh_server):
    client, seen = hh_server
    resume = await client.get_negotiation_resume("token", "123")
    assert resume.last_name == "Иванов"
    assert seen["headers"]["Authorization"] == "Bearer token"
    assert seen["headers"]["HH-User-Agent"] == "test-agent"


async def test_api_error_carries_status_and_type(hh_server):
    client, _ = hh_server
    with pytest.raises(HHAPIError, match="not_found") as exc_info:
        await client.get_negotiation_resume("token", "404")
    assert exc_info.value.status_code == 404


async def test_negotiation_without_resume(hh_server):
    client, _ = hh_server
    with pytest.raises(HHUnexpectedResponse):
        await client.get_negotiation_resume("token", "deleted")


async def test_token_is_not_sent_to_foreign_host(hh_server):
    client, _ = hh_server
    with pytest.raises(HHUnexpectedResponse, match="Unexpected host"):
        await client.get_negotiation_resume("token", "foreign")


async def test_exchange_code_sends_pkce_verifier(hh_server):
    client, seen = hh_server
    token = await client.exchange_code("CODE", "VERIFIER")
    assert (token.access_token, token.refresh_token) == ("A", "R")
    assert seen["token_form"] == {
        "grant_type": "authorization_code",
        "client_id": "cid",
        "client_secret": "secret",
        "redirect_uri": "https://x/cb",
        "code": "CODE",
        "code_verifier": "VERIFIER",
    }


async def test_refresh_oauth_error_is_readable(hh_server):
    client, _ = hh_server
    with pytest.raises(HHAPIError, match="invalid_grant"):
        await client.refresh_token("used")


def test_auth_flow_state_is_single_use():
    flow = HHAuthFlow("cid", "https://x/cb")
    query = parse_qs(urlparse(flow.start(hr_tg_id=7)).query)
    assert query["role"] == ["employer"]
    assert query["code_challenge_method"] == ["S256"]
    state = query["state"][0]
    pending = flow.pop(state)
    assert pending.hr_tg_id == 7
    assert flow.pop(state) is None


def test_resume_to_text():
    text = HHResume.model_validate(RESUME).to_text()
    assert text.startswith("Иванов Иван")
    assert "Общий стаж: 5 лет 2 мес." in text
    assert "Навыки: Python, RAG" in text
    assert "- 2021-01-01 — по настоящее время, HeadHunter, ML Engineer" in text
    assert "Делал матчинг резюме" in text
    assert "Языки: Английский (B2)" in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("123456789", "123456789"),
        (" 123 ", "123"),
        ("https://hh.ru/resume/0123abc?t=987&vacancyId=1", "987"),
        ("https://spb.hh.ru/resume/0123abc?t=987", "987"),
        ("https://hh.ru/resume/0123abc", None),
        ("https://evilhh.ru/resume/0123abc?t=987", None),
        ("Иванов Иван, Python-разработчик", None),
    ],
)
def test_parse_negotiation_id(text, expected):
    assert parse_negotiation_id(text) == expected
