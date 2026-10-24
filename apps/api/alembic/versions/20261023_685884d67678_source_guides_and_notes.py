"""source guides and notes

Revision ID: 685884d67678
Revises: da23fbedb213
Create Date: 2026-10-23 17:00:25.149060
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "685884d67678"
down_revision: str | None = "da23fbedb213"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notebook_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ready", nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("passages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('manual', 'answer', 'study_guide', 'faq', 'briefing', 'timeline')",
            name=op.f("ck_notes_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'generating', 'failed')", name=op.f("ck_notes_status")
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"],
            ["notebooks.id"],
            name=op.f("fk_notes_notebook_id_notebooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
    )
    op.create_index("ix_notes_notebook_id_created_at", "notes", ["notebook_id", "created_at"])

    op.add_column("sources", sa.Column("guide_status", sa.String(length=20), nullable=True))
    op.add_column(
        "sources", sa.Column("guide", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    # Autogenerate doesn't detect check constraints added to an existing table.
    op.create_check_constraint(
        op.f("ck_sources_guide_status"), "sources", "guide_status IN ('pending', 'ready', 'failed')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sources_guide_status"), "sources", type_="check")
    op.drop_column("sources", "guide")
    op.drop_column("sources", "guide_status")
    op.drop_index("ix_notes_notebook_id_created_at", table_name="notes")
    op.drop_table("notes")
