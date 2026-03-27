"""add discovered nodes table

Revision ID: 20260327_0005
Revises: 20260324_0004
Create Date: 2026-03-27 14:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260327_0005"
down_revision = "20260324_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovered_nodes",
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("site_name", sa.String(length=255), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("discovered_level", sa.Integer(), nullable=True),
        sa.Column("discovered_parent_site_id", sa.String(length=64), nullable=True),
        sa.Column("discovered_parent_name", sa.String(length=255), nullable=True),
        sa.Column("surfaced_by_names_json", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tx_bps", sa.Integer(), nullable=True),
        sa.Column("rx_bps", sa.Integer(), nullable=True),
        sa.Column("tx_display", sa.String(length=64), nullable=True),
        sa.Column("rx_display", sa.String(length=64), nullable=True),
        sa.Column("web_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ssh_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ping", sa.String(length=16), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ping_up", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ping_down_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("site_id"),
    )
    op.create_index(op.f("ix_discovered_nodes_site_id"), "discovered_nodes", ["site_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_discovered_nodes_site_id"), table_name="discovered_nodes")
    op.drop_table("discovered_nodes")
