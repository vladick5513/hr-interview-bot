from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, BigIntPK, str_enum
from app.enums import QuestionSource


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (UniqueConstraint("hh_vacancy_id", name="uq_vacancies_hh_vacancy_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    owner_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    hh_vacancy_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    criteria: Mapped[list["VacancyCriterion"]] = relationship(
        order_by="VacancyCriterion.id", lazy="raise"
    )
    questions: Mapped[list["VacancyQuestion"]] = relationship(
        order_by="VacancyQuestion.position", lazy="raise"
    )


class VacancyCriterion(Base):
    __tablename__ = "vacancy_criteria"
    __table_args__ = (Index("ix_vacancy_criteria_vacancy_id", "vacancy_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vacancies.id", ondelete="CASCADE", name="fk_vacancy_criteria_vacancy_id"),
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(SmallInteger)
    is_must_have: Mapped[bool]


class VacancyQuestion(Base):
    __tablename__ = "vacancy_questions"
    __table_args__ = (Index("ix_vacancy_questions_vacancy_id", "vacancy_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vacancies.id", ondelete="CASCADE", name="fk_vacancy_questions_vacancy_id"),
    )
    criterion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "vacancy_criteria.id", ondelete="CASCADE", name="fk_vacancy_questions_criterion_id"
        ),
    )
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[QuestionSource] = mapped_column(str_enum(QuestionSource))
    position: Mapped[int] = mapped_column(SmallInteger)
