"""add node relationships table

Revision ID: 20260327_0008
Revises: 20260327_0007
Create Date: 2026-03-27 21:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260327_0008"
down_revision = "20260327_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_relationships",
        sa.Column("source_site_id", sa.String(length=64), nullable=False),
        sa.Column("target_site_id", sa.String(length=64), nullable=False),
        sa.Column("relationship_kind", sa.String(length=32), nullable=False),
        sa.Column("source_row_type", sa.String(length=16), nullable=False),
        sa.Column("target_row_type", sa.String(length=16), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("target_name", sa.String(length=255), nullable=True),
        sa.Column("target_unit", sa.String(length=64), nullable=True),
        sa.Column("target_location", sa.String(length=255), nullable=True),
        sa.Column("discovered_level", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_site_id", "target_site_id", "relationship_kind"),
    )
    op.create_index(
        op.f("ix_node_relationships_target_site_id"),
        "node_relationships",
        ["target_site_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_node_relationships_relationship_kind"),
        "node_relationships",
        ["relationship_kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_node_relationships_relationship_kind"), table_name="node_relationships")
    op.drop_index(op.f("ix_node_relationships_target_site_id"), table_name="node_relationships")
    op.drop_table("node_relationships")
