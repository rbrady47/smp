"""add discovered_node map columns

Revision ID: 20260401_0014
Revises: 20260331_0013
Create Date: 2026-04-01 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260401_0014"
down_revision = "20260331_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovered_nodes", sa.Column("map_view_id", sa.Integer(), sa.ForeignKey("operational_map_views.id"), nullable=True))
    op.add_column("discovered_nodes", sa.Column("map_x", sa.Integer(), nullable=True))
    op.add_column("discovered_nodes", sa.Column("map_y", sa.Integer(), nullable=True))
    op.add_column("discovered_nodes", sa.Column("source_anchor_node_id", sa.Integer(), nullable=True))
    op.create_index("ix_discovered_nodes_map_view_id", "discovered_nodes", ["map_view_id"])


def downgrade() -> None:
    op.drop_index("ix_discovered_nodes_map_view_id", "discovered_nodes")
    op.drop_column("discovered_nodes", "source_anchor_node_id")
    op.drop_column("discovered_nodes", "map_y")
    op.drop_column("discovered_nodes", "map_x")
    op.drop_column("discovered_nodes", "map_view_id")
