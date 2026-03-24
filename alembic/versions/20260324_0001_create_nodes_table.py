"""create nodes table

Revision ID: 20260324_0001
Revises:
Create Date: 2026-03-24 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260324_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("web_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_nodes_id", "nodes", ["id"])


def downgrade() -> None:
    op.drop_index("ix_nodes_id", table_name="nodes")
    op.drop_table("nodes")
