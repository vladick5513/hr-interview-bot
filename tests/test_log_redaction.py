import asyncio
import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from openai import OpenAIError
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.hh.client import HHClient
from app.hh.exceptions import HHUnexpectedResponse
from app.hh.worker import screening_worker
from app.llm.client import LLMClient
from app.llm.exceptions import LLMTransportError, LLMUnexpectedResponse
from app.llm.schemas import ResumeAnalysis
from app.logger import logger

MARKER = "SECRET_RESUME_TEXT"


class FailingCompletions:
    def __init__(self, error: Exception):
        self.error = error

    async def parse(self, **kwargs):
        raise self.error


def _llm(error: Exception) -> LLMClient:
    llm = LLMClient(api_key="test", model="test", base_url=None)
    llm.openai.chat.completions = FailingCompletions(error)
    return llm


async def test_llm_errors_do_not_leak_text():
    with pytest.raises(ValidationError) as invalid:
        ResumeAnalysis.model_validate({"summary": MARKER})
    assert MARKER in str(invalid.value)

    with pytest.raises(LLMUnexpectedResponse) as unexpected:
        await _llm(invalid.value)._parse("s", "u", ResumeAnalysis)
    assert MARKER not in str(unexpected.value)
    assert unexpected.value.__cause__ is invalid.value

    with pytest.raises(LLMTransportError) as transport:
        await _llm(OpenAIError(MARKER))._parse("s", "u", ResumeAnalysis)
    assert MARKER not in str(transport.value)
    assert isinstance(transport.value.__cause__, OpenAIError)


async def test_hh_invalid_json_does_not_leak_text():
    async def negotiation(request: web.Request) -> web.Response:
        return web.json_response({"id": 5, "resume": MARKER})

    app = web.Application()
    app.router.add_get("/negotiations/{nid}", negotiation)
    async with TestServer(app) as server:
        client = HHClient(str(server.make_url("")), "agent", "cid", "secret", "https://x/cb")
        try:
            with pytest.raises(HHUnexpectedResponse) as exc_info:
                await client.get_negotiation_resume("token", "1")
        finally:
            await client.close()
    assert MARKER not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValidationError)


async def test_worker_log_has_no_sql_parameters(caplog):
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=logger.name)

    def broken_factory():
        raise IntegrityError("INSERT INTO t VALUES (?)", (MARKER,), Exception(MARKER))

    task = asyncio.create_task(
        screening_worker(
            bot=None,
            llm=None,
            hh=None,
            session_factory=broken_factory,
            new_responses=asyncio.Event(),
        )
    )
    while not caplog.records:
        await asyncio.sleep(0)
    task.cancel()
    logger.removeHandler(caplog.handler)

    assert "IntegrityError" in caplog.text
    assert MARKER not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
