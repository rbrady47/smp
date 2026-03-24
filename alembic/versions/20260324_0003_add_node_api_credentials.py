"""add node api credentials

Revision ID: 20260324_0003
Revises: 20260324_0002
Create Date: 2026-03-24 14:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260324_0003"
down_revision: Union[str, Sequence[str], None] = "20260324_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("api_username", sa.String(length=255), nullable=True))
    op.add_column("nodes", sa.Column("api_password", sa.String(length=255), nullable=True))
    op.add_column("nodes", sa.Column("api_use_https", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("nodes", "api_use_https")
    op.drop_column("nodes", "api_password")
    op.drop_column("nodes", "api_username")
