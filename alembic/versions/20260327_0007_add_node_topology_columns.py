"""add node topology columns

Revision ID: 20260327_0007
Revises: 20260327_0006
Create Date: 2026-03-27 19:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260327_0007"
down_revision = "20260327_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("node_id", sa.String(length=64), nullable=True))
    op.add_column("nodes", sa.Column("include_in_topology", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("nodes", sa.Column("topology_level", sa.Integer(), nullable=True))
    op.add_column("nodes", sa.Column("topology_unit", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "topology_unit")
    op.drop_column("nodes", "topology_level")
    op.drop_column("nodes", "include_in_topology")
    op.drop_column("nodes", "node_id")
