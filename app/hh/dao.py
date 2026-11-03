from sqlalchemy import select

from app.dao.base import BaseDAO
from app.enums import ResponseStatus
from app.hh.models import HHResponse, HHToken


class HHTokenDAO(BaseDAO[HHToken]):
    model = HHToken

    async def get(self, hr_tg_id: int) -> HHToken | None:
        return await self.session.get(HHToken, hr_tg_id)


class HHResponseDAO(BaseDAO[HHResponse]):
    model = HHResponse

    async def get(self, response_id: int) -> HHResponse | None:
        return await self.session.get(HHResponse, response_id)

    async def exists(self, topic_id: str) -> bool:
        found = await self.session.scalar(
            select(HHResponse.id).where(HHResponse.topic_id == topic_id)
        )
        return found is not None

    async def find_new(self, limit: int) -> list[HHResponse]:
        result = await self.session.scalars(
            select(HHResponse)
            .where(HHResponse.status == ResponseStatus.NEW)
            .order_by(HHResponse.id)
            .limit(limit)
        )
        return list(result)

    async def find_screened(self, vacancy_id: int, limit: int) -> list[HHResponse]:
        result = await self.session.scalars(
            select(HHResponse)
            .where(
                HHResponse.vacancy_id == vacancy_id,
                HHResponse.status.in_(
                    [ResponseStatus.SCREENED, ResponseStatus.INVITED, ResponseStatus.REJECTED]
                ),
            )
            .order_by(HHResponse.resume_score.desc().nulls_last(), HHResponse.id)
            .limit(limit)
        )
        return list(result)
