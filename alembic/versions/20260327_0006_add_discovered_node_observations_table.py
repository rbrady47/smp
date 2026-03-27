"""add discovered node observations table

Revision ID: 20260327_0006
Revises: 20260327_0005
Create Date: 2026-03-27 16:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260327_0006"
down_revision = "20260327_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovered_node_observations",
        sa.Column("site_id", sa.String(length=64), nullable=False),
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
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["discovered_nodes.site_id"]),
        sa.PrimaryKeyConstraint("site_id"),
    )
    op.create_index(
        op.f("ix_discovered_node_observations_site_id"),
        "discovered_node_observations",
        ["site_id"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO discovered_node_observations (
            site_id,
            latency_ms,
            tx_bps,
            rx_bps,
            tx_display,
            rx_display,
            web_ok,
            ssh_ok,
            ping,
            last_seen,
            last_ping_up,
            ping_down_since,
            probed_at,
            detail_json,
            observed_at,
            created_at,
            updated_at
        )
        SELECT
            site_id,
            latency_ms,
            tx_bps,
            rx_bps,
            tx_display,
            rx_display,
            web_ok,
            ssh_ok,
            ping,
            last_seen,
            last_ping_up,
            ping_down_since,
            probed_at,
            detail_json,
            COALESCE(updated_at, created_at),
            created_at,
            updated_at
        FROM discovered_nodes
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_discovered_node_observations_site_id"), table_name="discovered_node_observations")
    op.drop_table("discovered_node_observations")
