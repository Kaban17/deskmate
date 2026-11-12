"""initial schema: users, dialogs, messages

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("full_name", sa.String(256), nullable=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="employee"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("external_id", name="uq_users_external_id"),
        sa.CheckConstraint("role IN ('employee', 'admin')", name="ck_users_role"),
    )
    op.create_table(
        "dialogs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('active', 'closed')", name="ck_dialogs_status"),
    )
    op.create_index(
        "uq_dialogs_one_active",
        "dialogs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dialog_id", sa.Integer(), sa.ForeignKey("dialogs.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_dialog_id_id", "messages", ["dialog_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_messages_dialog_id_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("uq_dialogs_one_active", table_name="dialogs")
    op.drop_table("dialogs")
    op.drop_table("users")
