import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import QuestionMode, QuestionSource
from app.llm.schemas import VacancyProfile
from app.logger import logger
from app.vacancy.dao import VacancyCriterionDAO, VacancyDAO, VacancyQuestionDAO
from app.vacancy.models import Vacancy

MAX_POOL_QUESTIONS = 50

_LIST_MARKER = re.compile(r"^\s*(?:\d+\s*[.)]|[-–—•*])\s*")


@dataclass(frozen=True)
class DraftQuestion:
    criterion_number: int
    text: str
    source: QuestionSource


@dataclass(frozen=True)
class QuestionsDraft:
    questions: list[DraftQuestion]
    unassigned: list[str]


def parse_question_pool(text: str) -> list[str]:
    lines = (_LIST_MARKER.sub("", line).strip() for line in text.splitlines())
    return [line for line in lines if line]


def assemble_questions(
    profile: VacancyProfile, pool: list[str], mode: QuestionMode
) -> QuestionsDraft:
    assigned: dict[int, int] = {}
    for match in profile.company_questions:
        if 1 <= match.question_number <= len(pool):
            assigned.setdefault(match.question_number, match.criterion_number)

    if mode is QuestionMode.GENERATE:
        generated = profile.questions
    elif mode is QuestionMode.COMPANY:
        generated = []
    else:
        covered = set(assigned.values())
        generated = [
            q
            for q in profile.questions
            if q.criterion_number not in covered
            and profile.criteria[q.criterion_number - 1].is_must_have
        ]

    questions = [
        DraftQuestion(criterion, pool[number - 1], QuestionSource.COMPANY)
        for number, criterion in sorted(assigned.items())
    ] + [DraftQuestion(q.criterion_number, q.text, QuestionSource.GENERATED) for q in generated]
    unassigned = [q for number, q in enumerate(pool, start=1) if number not in assigned]
    return QuestionsDraft(questions, unassigned)


class VacancyService:
    def __init__(self, session: AsyncSession):
        self.vacancies = VacancyDAO(session)
        self.criteria = VacancyCriterionDAO(session)
        self.questions = VacancyQuestionDAO(session)

    async def create(
        self,
        vacancy_text: str,
        profile: VacancyProfile,
        questions: list[DraftQuestion],
        owner_tg_id: int,
    ) -> Vacancy:
        vacancy = await self.vacancies.add(
            title=profile.title, description=vacancy_text, owner_tg_id=owner_tg_id
        )
        criteria = await self.criteria.add_many(
            [
                {
                    "vacancy_id": vacancy.id,
                    "title": c.title,
                    "description": c.description,
                    "weight": c.weight,
                    "is_must_have": c.is_must_have,
                }
                for c in profile.criteria
            ]
        )
        await self.questions.add_many(
            [
                {
                    "vacancy_id": vacancy.id,
                    "criterion_id": criteria[q.criterion_number - 1].id,
                    "text": q.text,
                    "source": q.source,
                    "position": position,
                }
                for position, q in enumerate(questions)
            ]
        )
        logger.info("Вакансия создана: %s", vacancy.id)
        return vacancy
