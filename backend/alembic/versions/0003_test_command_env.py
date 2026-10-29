"""test command environment allowlist

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("test_commands") as batch_op:
        batch_op.add_column(sa.Column("extra_env_names", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("inherit_full_env", sa.Boolean(), nullable=False, server_default="0"))
    # The approval hash now covers the environment fields: existing approvals must be re-reviewed.
    op.execute("UPDATE test_commands SET approved = 0, approved_hash = NULL, approved_at = NULL")


def downgrade() -> None:
    with op.batch_alter_table("test_commands") as batch_op:
        batch_op.drop_column("inherit_full_env")
        batch_op.drop_column("extra_env_names")
