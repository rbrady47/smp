"""add sample_type to chart_samples for decimation min/max pairs

Revision ID: 20260407_0016
Revises: 20260407_0015
Create Date: 2026-04-07 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0016"
down_revision = "20260407_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sample_type column (min/max/raw)
    op.add_column("chart_samples", sa.Column("sample_type", sa.String(8), nullable=False, server_default="raw"))

    # Drop old unique constraint and create new one including sample_type
    op.drop_constraint("uq_chart_samples_node_timestamp", "chart_samples", type_="unique")
    op.create_unique_constraint(
        "uq_chart_samples_node_ts_type",
        "chart_samples",
        ["node_id", "timestamp", "sample_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_chart_samples_node_ts_type", "chart_samples", type_="unique")
    op.create_unique_constraint(
        "uq_chart_samples_node_timestamp",
        "chart_samples",
        ["node_id", "timestamp"],
    )
    op.drop_column("chart_samples", "sample_type")
