"""add node check metadata

Revision ID: 20260324_0002
Revises: 20260324_0001
Create Date: 2026-03-24 13:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260324_0002"
down_revision: Union[str, Sequence[str], None] = "20260324_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("last_checked", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nodes", sa.Column("latency_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "latency_ms")
    op.drop_column("nodes", "last_checked")
