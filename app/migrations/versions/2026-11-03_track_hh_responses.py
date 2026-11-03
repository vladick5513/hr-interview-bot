"""track hh responses

Revision ID: 9393ffb66fa4
Revises: a38dbd3cb45d
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9393ffb66fa4"
down_revision: str | None = "a38dbd3cb45d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vacancies", sa.Column("owner_tg_id", sa.BigInteger(), nullable=True))
    op.add_column("vacancies", sa.Column("hh_vacancy_id", sa.String(length=32), nullable=True))
    op.create_unique_constraint("uq_vacancies_hh_vacancy_id", "vacancies", ["hh_vacancy_id"])

    op.create_table(
        "hh_responses",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.String(length=32), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "screened",
                "invited",
                "rejected",
                "failed",
                name="responsestatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("candidate_name", sa.String(length=255), nullable=True),
        sa.Column("resume_text", sa.Text(), nullable=True),
        sa.Column("resume_analysis", sa.JSON(), nullable=True),
        sa.Column("resume_score", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("interview_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("screened_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interviews.id"],
            name="fk_hh_responses_interview_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            name="fk_hh_responses_vacancy_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", name="uq_hh_responses_topic_id"),
    )
    op.create_index("ix_hh_responses_status", "hh_responses", ["status"])
    op.create_index("ix_hh_responses_vacancy_id_status", "hh_responses", ["vacancy_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_hh_responses_vacancy_id_status", table_name="hh_responses")
    op.drop_index("ix_hh_responses_status", table_name="hh_responses")
    op.drop_table("hh_responses")
    op.drop_constraint("uq_vacancies_hh_vacancy_id", "vacancies", type_="unique")
    op.drop_column("vacancies", "hh_vacancy_id")
    op.drop_column("vacancies", "owner_tg_id")
