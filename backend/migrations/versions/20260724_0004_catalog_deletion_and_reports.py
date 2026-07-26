"""Add safe catalog deletion flags and persistent deployment reports.

Revision ID: 20260724_0004
Revises: 20260724_0003
"""
import sqlalchemy as sa
from alembic import op


revision = "20260724_0004"
down_revision = "20260724_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "packages",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("packages", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("deployments", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_deployments_deleted_at",
        "deployments",
        ["deleted_at"],
        unique=False,
    )
    op.add_column("deployments", sa.Column("report_path", sa.String(512), nullable=True))
    op.add_column(
        "deployments",
        sa.Column("report_generated_at", sa.DateTime(), nullable=True),
    )
    op.add_column("deployments", sa.Column("report_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("deployments", "report_snapshot")
    op.drop_column("deployments", "report_generated_at")
    op.drop_column("deployments", "report_path")
    op.drop_index("ix_deployments_deleted_at", table_name="deployments")
    op.drop_column("deployments", "deleted_at")
    op.drop_column("packages", "deleted_at")
    op.drop_column("packages", "enabled")
