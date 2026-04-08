"""add charts_enabled to nodes

Revision ID: 20260408_0017
Revises: 20260407_0016
Create Date: 2026-04-08 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260408_0017"
down_revision = "20260407_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("charts_enabled", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    op.drop_column("nodes", "charts_enabled")
