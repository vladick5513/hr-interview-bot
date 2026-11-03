import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dao.base import BaseDAO
from app.enums import InterviewStatus
from app.interview.models import Interview, InterviewQuestion
from app.vacancy.models import Vacancy

_FULL = (
    selectinload(Interview.questions),
    selectinload(Interview.vacancy).selectinload(Vacancy.criteria),
)


class InterviewDAO(BaseDAO[Interview]):
    model = Interview

    async def get_full(self, interview_id: uuid.UUID) -> Interview | None:
        return await self.session.scalar(
            select(Interview).where(Interview.id == interview_id).options(*_FULL)
        )

    async def find_active_for_candidate(self, candidate_tg_id: int) -> Interview | None:
        return await self.session.scalar(
            select(Interview)
            .where(
                Interview.candidate_tg_id == candidate_tg_id,
                Interview.status == InterviewStatus.IN_PROGRESS,
            )
            .order_by(Interview.started_at.desc())
            .limit(1)
            .options(*_FULL)
        )


class InterviewQuestionDAO(BaseDAO[InterviewQuestion]):
    model = InterviewQuestion
