import hashlib
import hmac
import re
from typing import Any
from urllib.parse import ParseResult, parse_qs, urlparse

import httpx
from pydantic import BaseModel, ValidationError

from app.hh.exceptions import HHAPIError, HHTransportError, HHUnexpectedResponse
from app.hh.schemas import (
    HHNegotiation,
    HHResume,
    HHSubscriptions,
    HHTokenResponse,
    HHVacancy,
)

HH_API_URL = "https://api.hh.ru"
NEW_NEGOTIATION_ACTION = "NEW_NEGOTIATION_VACANCY"
WEBHOOK_PATH = "/hh/webhook"


def webhook_secret(client_secret: str) -> str:
    return hmac.new(client_secret.encode(), b"hh-webhook", hashlib.sha256).hexdigest()[:32]


def webhook_url(redirect_uri: str, client_secret: str) -> str:
    origin = urlparse(redirect_uri)
    return f"{origin.scheme}://{origin.netloc}{WEBHOOK_PATH}?secret={webhook_secret(client_secret)}"


def _hh_url(text: str) -> ParseResult | None:
    url = urlparse(text)
    hostname = url.hostname or ""
    return url if hostname == "hh.ru" or hostname.endswith(".hh.ru") else None


def parse_negotiation_id(text: str) -> str | None:
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        return text
    url = _hh_url(text)
    if url is not None and url.path.startswith("/resume/"):
        topic = parse_qs(url.query).get("t", [""])[0]
        if topic.isdigit():
            return topic
    return None


def parse_vacancy_id(text: str) -> str | None:
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        return text
    url = _hh_url(text)
    if url is not None:
        match = re.fullmatch(r"/vacancy/(\d+)/?", url.path)
        if match:
            return match.group(1)
    return None


class HHClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ):
        self.http = httpx.AsyncClient(
            base_url=base_url, headers={"HH-User-Agent": user_agent}, timeout=10
        )
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    async def close(self) -> None:
        await self.http.aclose()

    async def exchange_code(self, code: str, code_verifier: str) -> HHTokenResponse:
        return await self._request(
            "POST",
            "/token",
            HHTokenResponse,
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
        )

    async def refresh_token(self, refresh_token: str) -> HHTokenResponse:
        return await self._request(
            "POST",
            "/token",
            HHTokenResponse,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

    async def get_negotiation_resume(self, access_token: str, negotiation_id: str) -> HHResume:
        negotiation = await self._get(
            access_token, f"/negotiations/{negotiation_id}", HHNegotiation
        )
        if negotiation.resume is None:
            raise HHUnexpectedResponse("Резюме в отклике недоступно")
        return await self._get(access_token, negotiation.resume.url, HHResume)

    async def get_vacancy(self, access_token: str, vacancy_id: str) -> HHVacancy:
        return await self._get(access_token, f"/vacancies/{vacancy_id}", HHVacancy)

    async def get_subscriptions(self, access_token: str) -> HHSubscriptions:
        return await self._get(access_token, "/webhook/subscriptions", HHSubscriptions)

    async def subscribe_new_responses(self, access_token: str, url: str) -> None:
        try:
            response = await self.http.post(
                "/webhook/subscriptions",
                json={"url": url, "actions": [{"type": NEW_NEGOTIATION_ACTION}]},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise HHTransportError(f"Ошибка запроса к hh.ru: {type(exc).__name__}") from exc
        if response.status_code not in (200, 201, 204):
            raise HHAPIError(response.status_code, _error_types(response))

    async def _get[SchemaT: BaseModel](
        self, access_token: str, url: str, schema: type[SchemaT]
    ) -> SchemaT:
        # Токен уходит в заголовке, поэтому чужие хосты из ответа не запрашиваем
        target = httpx.URL(url)
        if target.is_absolute_url and target.host != self.http.base_url.host:
            raise HHUnexpectedResponse(f"Unexpected host in {url}")
        return await self._request(
            "GET", url, schema, headers={"Authorization": f"Bearer {access_token}"}
        )

    async def _request[SchemaT: BaseModel](
        self, method: str, url: str, schema: type[SchemaT], **kwargs: Any
    ) -> SchemaT:
        try:
            response = await self.http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise HHTransportError(f"Ошибка запроса к hh.ru: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise HHAPIError(response.status_code, _error_types(response))
        try:
            return schema.model_validate_json(response.content)
        except ValidationError as exc:
            raise HHUnexpectedResponse(f"Некорректный ответ hh.ru: {type(exc).__name__}") from exc


def _error_types(response: httpx.Response) -> list[str]:
    try:
        body = response.json()
    except ValueError:
        return []
    if not isinstance(body, dict):
        return []
    if "error" in body:
        return [str(body["error"]), str(body.get("error_description", ""))]
    return [str(e.get("type", "")) for e in body.get("errors", []) if isinstance(e, dict)]
