from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.hh.client import NEW_NEGOTIATION_ACTION, HHClient
from app.hh.dao import HHTokenDAO
from app.hh.exceptions import HHNotConnected
from app.hh.schemas import HHResume, HHTokenResponse, HHVacancy


class HHService:
    def __init__(self, session: AsyncSession, hh: HHClient):
        self.session = session
        self.hh = hh
        self.tokens = HHTokenDAO(session)

    async def save_tokens(self, hr_tg_id: int, token: HHTokenResponse) -> None:
        values = {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": datetime.now(UTC) + timedelta(seconds=token.expires_in),
        }
        row = await self.tokens.get(hr_tg_id)
        if row is None:
            await self.tokens.add(hr_tg_id=hr_tg_id, **values)
            return
        for key, value in values.items():
            setattr(row, key, value)

    async def get_negotiation_resume(self, hr_tg_id: int, negotiation_id: str) -> HHResume:
        token = await self._access_token(hr_tg_id)
        return await self.hh.get_negotiation_resume(token, negotiation_id)

    async def get_vacancy(self, hr_tg_id: int, vacancy_id: str) -> HHVacancy:
        token = await self._access_token(hr_tg_id)
        return await self.hh.get_vacancy(token, vacancy_id)

    async def ensure_subscription(self, hr_tg_id: int, url: str) -> None:
        token = await self._access_token(hr_tg_id)
        subscriptions = await self.hh.get_subscriptions(token)
        for subscription in subscriptions.items:
            actions = {a.type for a in subscription.actions}
            if subscription.url == url and NEW_NEGOTIATION_ACTION in actions:
                return
        await self.hh.subscribe_new_responses(token, url)

    async def _access_token(self, hr_tg_id: int) -> str:
        row = await self.tokens.get(hr_tg_id)
        if row is None:
            raise HHNotConnected("hh.ru не подключён")
        await self.session.commit()

        if row.expires_at <= datetime.now(UTC):
            token = await self.hh.refresh_token(row.refresh_token)
            await self.save_tokens(hr_tg_id, token)
            # Старый refresh уже сгорел: новую пару фиксируем, даже если дальше что-то упадёт
            await self.session.commit()
        return row.access_token
