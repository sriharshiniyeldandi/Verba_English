"""Add password_reset_codes: 6-digit email recovery codes for forgot-password flow.

Revision ID: 0007_password_reset_codes
Revises: 0006_progress_completed_at

Additive and non-destructive: one new table, no existing table or row is touched.
Idempotent on purpose, like revisions 0003-0006: revision 0001 builds fresh databases with
create_all() from the current models, so on a brand-new database the table already exists.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_password_reset_codes"
down_revision = "0006_progress_completed_at"
branch_labels = None
depends_on = None

TABLE = "password_reset_codes"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if TABLE in _tables():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_password_reset_codes_user_id", TABLE, ["user_id"])
    op.create_index("ix_password_reset_codes_expires_at", TABLE, ["expires_at"])
    op.create_index("ix_password_reset_codes_user_expires", TABLE, ["user_id", "expires_at"])


def downgrade():
    if TABLE in _tables():
        op.drop_table(TABLE)
