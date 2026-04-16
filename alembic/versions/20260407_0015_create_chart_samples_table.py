"""create chart_samples table

Revision ID: 20260407_0015
Revises: 20260401_0014
Create Date: 2026-04-07 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0015"
down_revision = "20260401_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chart_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("timestamp", sa.Integer(), nullable=False),
        sa.Column("user_tx_bytes", sa.Integer(), nullable=True),
        sa.Column("user_tx_pkts", sa.Integer(), nullable=True),
        sa.Column("user_rx_bytes", sa.Integer(), nullable=True),
        sa.Column("user_rx_pkts", sa.Integer(), nullable=True),
        sa.Column("channel_data", sa.Text(), nullable=True),
        sa.Column("tunnel_data", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "timestamp", name="uq_chart_samples_node_timestamp"),
    )
    op.create_index("ix_chart_samples_id", "chart_samples", ["id"])
    op.create_index("ix_chart_samples_node_id", "chart_samples", ["node_id"])
    op.create_index("ix_chart_samples_timestamp", "chart_samples", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_chart_samples_timestamp", table_name="chart_samples")
    op.drop_index("ix_chart_samples_node_id", table_name="chart_samples")
    op.drop_index("ix_chart_samples_id", table_name="chart_samples")
    op.drop_table("chart_samples")
