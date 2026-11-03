"""add question source

Revision ID: a38dbd3cb45d
Revises: 4d3f01597858
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a38dbd3cb45d"
down_revision: str | None = "4d3f01597858"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vacancy_questions",
        sa.Column(
            "source",
            sa.Enum(
                "company",
                "generated",
                "resume",
                name="questionsource",
                native_enum=False,
                length=20,
            ),
            server_default="generated",
            nullable=False,
        ),
    )
    op.alter_column("vacancy_questions", "source", server_default=None)
    op.execute("UPDATE interview_questions SET source = 'generated' WHERE source = 'mandatory'")


def downgrade() -> None:
    op.execute(
        "UPDATE interview_questions SET source = 'mandatory' "
        "WHERE source IN ('company', 'generated')"
    )
    op.drop_column("vacancy_questions", "source")
