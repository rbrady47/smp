"""add topology editor state table

Revision ID: 20260329_0010
Revises: 20260329_0009
Create Date: 2026-03-29 16:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260329_0010"
down_revision = "20260329_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topology_editor_state",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("layout_overrides_json", sa.Text(), nullable=True),
        sa.Column("state_log_layout_json", sa.Text(), nullable=True),
        sa.Column("link_anchor_assignments_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope"),
    )


def downgrade() -> None:
    op.drop_table("topology_editor_state")
