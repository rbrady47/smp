"""default api use https false

Revision ID: 20260324_0004
Revises: 20260324_0003
Create Date: 2026-03-24 15:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260324_0004"
down_revision: Union[str, Sequence[str], None] = "20260324_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("nodes", "api_use_https", server_default=sa.false(), existing_type=sa.Boolean())


def downgrade() -> None:
    op.alter_column("nodes", "api_use_https", server_default=sa.true(), existing_type=sa.Boolean())
