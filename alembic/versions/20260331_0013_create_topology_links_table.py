"""create topology_links table

Revision ID: 20260331_0013
Revises: 20260331_0012
Create Date: 2026-03-31 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260331_0013"
down_revision = "20260331_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topology_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_entity_id", sa.String(128), nullable=False),
        sa.Column("target_entity_id", sa.String(128), nullable=False),
        sa.Column("source_anchor", sa.String(8), nullable=False, server_default="e"),
        sa.Column("target_anchor", sa.String(8), nullable=False, server_default="w"),
        sa.Column("link_type", sa.String(16), nullable=False, server_default="solid"),
        sa.Column("status_node_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("topology_links")
