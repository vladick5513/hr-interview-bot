import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.vacancy.models import Vacancy, VacancyCriterion, VacancyQuestion


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def vacancy(session) -> Vacancy:
    vacancy = Vacancy(id=1, title="AI lead", description="LLM")
    session.add_all(
        [
            vacancy,
            VacancyCriterion(
                id=1, vacancy_id=1, title="LLM", description="RAG", weight=3, is_must_have=True
            ),
            VacancyCriterion(
                id=2, vacancy_id=1, title="Python", description="", weight=1, is_must_have=False
            ),
            VacancyQuestion(
                vacancy_id=1, criterion_id=1, position=0, text="Про LLM", source="company"
            ),
            VacancyQuestion(
                vacancy_id=1, criterion_id=2, position=1, text="Про Python", source="generated"
            ),
        ]
    )
    await session.commit()
    return vacancy
