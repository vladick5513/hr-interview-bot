"""init

Revision ID: 4d3f01597858
Revises:
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d3f01597858"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hh_tokens",
        sa.Column("hr_tg_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("hr_tg_id"),
    )
    op.create_table(
        "vacancies",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "interviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("hr_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_name", sa.String(length=255), nullable=False),
        sa.Column("hh_negotiation_id", sa.String(length=32), nullable=True),
        sa.Column("resume_text", sa.Text(), nullable=False),
        sa.Column("resume_analysis", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "in_progress",
                "finished",
                name="interviewstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column(
            "verdict",
            sa.Enum(
                "recommended", "consider", "rejected", name="verdict", native_enum=False, length=20
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vacancy_id"], ["vacancies.id"], name="fk_interviews_vacancy_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interviews_candidate_tg_id", "interviews", ["candidate_tg_id"], unique=False
    )
    op.create_index("ix_interviews_vacancy_id", "interviews", ["vacancy_id"], unique=False)
    op.create_table(
        "vacancy_criteria",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("weight", sa.SmallInteger(), nullable=False),
        sa.Column("is_must_have", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            name="fk_vacancy_criteria_vacancy_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vacancy_criteria_vacancy_id", "vacancy_criteria", ["vacancy_id"], unique=False
    )
    op.create_table(
        "interview_questions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("interview_id", sa.Uuid(), nullable=False),
        sa.Column("criterion_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("mandatory", "resume", name="questionsource", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("follow_up", sa.Text(), nullable=True),
        sa.Column("follow_up_answer", sa.Text(), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("evidence_found", sa.Boolean(), nullable=True),
        sa.Column("discrepancy", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["criterion_id"], ["vacancy_criteria.id"], name="fk_interview_questions_criterion_id"
        ),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interviews.id"],
            name="fk_interview_questions_interview_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_questions_interview_id", "interview_questions", ["interview_id"], unique=False
    )
    op.create_table(
        "vacancy_questions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("criterion_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["criterion_id"],
            ["vacancy_criteria.id"],
            name="fk_vacancy_questions_criterion_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            name="fk_vacancy_questions_vacancy_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vacancy_questions_vacancy_id", "vacancy_questions", ["vacancy_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_vacancy_questions_vacancy_id", table_name="vacancy_questions")
    op.drop_table("vacancy_questions")
    op.drop_index("ix_interview_questions_interview_id", table_name="interview_questions")
    op.drop_table("interview_questions")
    op.drop_index("ix_vacancy_criteria_vacancy_id", table_name="vacancy_criteria")
    op.drop_table("vacancy_criteria")
    op.drop_index("ix_interviews_vacancy_id", table_name="interviews")
    op.drop_index("ix_interviews_candidate_tg_id", table_name="interviews")
    op.drop_table("interviews")
    op.drop_table("vacancies")
    op.drop_table("hh_tokens")
