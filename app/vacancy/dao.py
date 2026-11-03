from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dao.base import BaseDAO
from app.vacancy.models import Vacancy, VacancyCriterion, VacancyQuestion


class VacancyCriterionDAO(BaseDAO[VacancyCriterion]):
    model = VacancyCriterion


class VacancyQuestionDAO(BaseDAO[VacancyQuestion]):
    model = VacancyQuestion


class VacancyDAO(BaseDAO[Vacancy]):
    model = Vacancy

    async def find_by_hh_id(self, hh_vacancy_id: str) -> Vacancy | None:
        return await self.session.scalar(
            select(Vacancy).where(Vacancy.hh_vacancy_id == hh_vacancy_id)
        )

    async def find_all(self) -> list[Vacancy]:
        result = await self.session.scalars(select(Vacancy).order_by(Vacancy.id))
        return list(result)

    async def get_with_profile(self, vacancy_id: int) -> Vacancy | None:
        return await self.session.scalar(
            select(Vacancy)
            .where(Vacancy.id == vacancy_id)
            .options(selectinload(Vacancy.criteria), selectinload(Vacancy.questions))
        )
