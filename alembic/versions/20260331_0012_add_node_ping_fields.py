"""add node ping_enabled and ping_interval_seconds

Revision ID: 20260331_0012
Revises: 20260329_0011
Create Date: 2026-03-31 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260331_0012"
down_revision = "20260329_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("ping_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("nodes", sa.Column("ping_interval_seconds", sa.Integer(), nullable=False, server_default="15"))


def downgrade() -> None:
    op.drop_column("nodes", "ping_interval_seconds")
    op.drop_column("nodes", "ping_enabled")
