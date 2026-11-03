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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, BigIntPK, UTCDateTime, str_enum
from app.enums import ResponseStatus


class HHToken(Base):
    __tablename__ = "hh_tokens"

    hr_tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HHResponse(Base):
    __tablename__ = "hh_responses"
    __table_args__ = (
        UniqueConstraint("topic_id", name="uq_hh_responses_topic_id"),
        Index("ix_hh_responses_vacancy_id_status", "vacancy_id", "status"),
        Index("ix_hh_responses_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    topic_id: Mapped[str] = mapped_column(String(32))
    vacancy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vacancies.id", ondelete="CASCADE", name="fk_hh_responses_vacancy_id"),
    )
    status: Mapped[ResponseStatus] = mapped_column(str_enum(ResponseStatus))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    candidate_name: Mapped[str | None] = mapped_column(String(255))
    resume_text: Mapped[str | None] = mapped_column(Text)
    resume_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    resume_score: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    interview_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="SET NULL", name="fk_hh_responses_interview_id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    screened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
