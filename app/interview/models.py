import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, BigIntPK, str_enum
from app.enums import InterviewStatus, QuestionSource, Verdict
from app.vacancy.models import Vacancy


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (
        Index("ix_interviews_vacancy_id", "vacancy_id"),
        Index("ix_interviews_candidate_tg_id", "candidate_tg_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vacancy_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vacancies.id", name="fk_interviews_vacancy_id")
    )
    hr_tg_id: Mapped[int] = mapped_column(BigInteger)
    candidate_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    candidate_name: Mapped[str] = mapped_column(String(255))
    hh_negotiation_id: Mapped[str | None] = mapped_column(String(32))
    resume_text: Mapped[str] = mapped_column(Text)
    resume_analysis: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[InterviewStatus] = mapped_column(str_enum(InterviewStatus))
    total_score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[Verdict | None] = mapped_column(str_enum(Verdict))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vacancy: Mapped[Vacancy] = relationship(lazy="raise")
    questions: Mapped[list["InterviewQuestion"]] = relationship(
        order_by="InterviewQuestion.position", lazy="raise"
    )


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"
    __table_args__ = (Index("ix_interview_questions_interview_id", "interview_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    interview_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE", name="fk_interview_questions_interview_id")
    )
    criterion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vacancy_criteria.id", name="fk_interview_questions_criterion_id"),
    )
    source: Mapped[QuestionSource] = mapped_column(str_enum(QuestionSource))
    position: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    follow_up: Mapped[str | None] = mapped_column(Text)
    follow_up_answer: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(SmallInteger)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    evidence_found: Mapped[bool | None]
    discrepancy: Mapped[str | None] = mapped_column(Text)

    @property
    def is_done(self) -> bool:
        return self.score is not None and (
            self.follow_up is None or self.follow_up_answer is not None
        )
