"""add composite index on chart_samples for query performance

Revision ID: 20260410_0018
Revises: 20260408_0017
Create Date: 2026-04-10 12:00:00.000000
"""

from alembic import op


revision = "20260410_0018"
down_revision = "20260408_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_chart_samples_node_ts_type",
        "chart_samples",
        ["node_id", "timestamp", "sample_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_chart_samples_node_ts_type", table_name="chart_samples")
