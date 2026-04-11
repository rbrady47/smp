"""add topology editor state demo mode

Revision ID: 20260329_0011
Revises: 20260329_0010
Create Date: 2026-03-29 15:55:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260329_0011"
down_revision = "20260329_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topology_editor_state", sa.Column("demo_mode_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("topology_editor_state", "demo_mode_json")
