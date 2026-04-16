"""Replace topology skeleton fields with map assignment.

Revision ID: 0019
Revises: 20260410_0018
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "20260416_0019"
down_revision = "20260410_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("topology_map_id", sa.Integer(), nullable=True))
    op.create_index("ix_nodes_topology_map_id", "nodes", ["topology_map_id"])

    op.execute(
        """
        UPDATE nodes
        SET topology_map_id = CASE
            WHEN include_in_topology = true THEN 0
            ELSE NULL
        END
        """
    )

    op.drop_column("nodes", "include_in_topology")
    op.drop_column("nodes", "topology_level")
    op.drop_column("nodes", "topology_unit")


def downgrade():
    op.add_column("nodes", sa.Column("topology_unit", sa.String(64), nullable=True))
    op.add_column("nodes", sa.Column("topology_level", sa.Integer(), nullable=True))
    op.add_column(
        "nodes",
        sa.Column("include_in_topology", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute("UPDATE nodes SET include_in_topology = true WHERE topology_map_id IS NOT NULL")
    op.execute("UPDATE nodes SET topology_level = 0 WHERE topology_map_id = 0")
    op.drop_index("ix_nodes_topology_map_id", table_name="nodes")
    op.drop_column("nodes", "topology_map_id")
